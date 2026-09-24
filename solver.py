import asyncio
import random
import time
import logging
import re
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, Page, Response

import config
from firebase_sync import FirebaseClient

logger = logging.getLogger("InstaLingBot.Solver")

class InstaLingSolver:
    def __init__(self, firebase_client: FirebaseClient, headless: bool = config.HEADLESS):
        self.firebase = firebase_client
        self.headless = headless

    async def human_type(self, page: Page, selector: str, text: str):
        """Wpisuje tekst znak po znaku z realistycznymi opóźnieniami człowieka"""
        try:
            await page.click(selector)
            await asyncio.sleep(random.uniform(0.1, 0.25))
            await page.fill(selector, "")
            await asyncio.sleep(random.uniform(0.05, 0.15))
            for char in text:
                delay_ms = random.randint(config.TYPING_SPEED_MIN, config.TYPING_SPEED_MAX)
                if char in (" ", "-", "'"):
                    delay_ms += random.randint(60, 150)
                await page.type(selector, char, delay=delay_ms)
                # Sporadyczne zawahanie przy wpisywaniu
                if random.random() < 0.08:
                    await asyncio.sleep(random.uniform(0.15, 0.35))
                else:
                    await asyncio.sleep(random.uniform(0.01, 0.03))
        except Exception as e:
            logger.warning(f"human_type fallback: {e}")
            await page.fill(selector, text)

    async def solve_session(self, login: str, password: str, preferred_lang: str = "auto") -> Dict[str, Any]:
        """Główna funkcja wykonująca całą sesję InstaLing dla podanego konta"""
        start_time = time.time()
        result = {
            "success": False,
            "message": "",
            "total_words": 0,
            "correct_words": 0,
            "learned_words": 0,
            "language": preferred_lang.upper(),
            "time_seconds": 0.0,
            "already_done": False
        }

        captured_correct_word: Optional[str] = None
        current_detected_lang: str = preferred_lang.lower() if preferred_lang != "auto" else "en"

        async with async_playwright() as p:
            # Uruchomienie z maskowaniem automatyzacji (pełna niewykrywalność)
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-infobars"
                ]
            )
            context = await browser.new_context(
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
                viewport={"width": 1280, "height": 800},
                locale="pl-PL"
            )

            # Usuwanie flagi navigator.webdriver oraz nakładek RODO / Cookies
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', {get: () => undefined});
                const cleanOverlays = () => {
                    const selectors = ['.fc-consent-root', '.fc-dialog-overlay', '.fc-dialog-container', '.modal-backdrop', '[class*="consent"]', '#credential_picker_container'];
                    selectors.forEach(s => document.querySelectorAll(s).forEach(el => el.remove()));
                    if (document.body) {
                        document.body.style.overflow = 'auto';
                        document.body.style.pointerEvents = 'auto';
                    }
                };
                window.addEventListener('DOMContentLoaded', cleanOverlays);
                setInterval(cleanOverlays, 300);
            """)

            page = await context.new_page()

            # Blokuj niepotrzebne pliki audio (.mp3) oraz skrypty reklamowe
            await page.route("**/*.mp3", lambda route: route.abort())
            await page.route("**/*fundingchoices*", lambda route: route.abort())

            # Nasłuchiwanie odpowiedzi sieciowych (odpowiednik XHR interceptora)
            def extract_word_from_dict(obj):
                if not obj or not isinstance(obj, dict):
                    return None
                fields = ["word", "correct", "answer", "translation", "correct_answer", "right", "solution"]
                for f in fields:
                    val = obj.get(f)
                    if isinstance(val, str) and val.strip() and len(val.strip()) < 120:
                        return val.strip()
                for v in obj.values():
                    if isinstance(v, dict):
                        found = extract_word_from_dict(v)
                        if found:
                            return found
                return None

            async def handle_response(response: Response):
                nonlocal captured_correct_word
                try:
                    url = response.url.lower()
                    if "app.php" in url or "action" in url or "check" in url:
                        content_type = response.headers.get("content-type", "").lower()
                        if "application/json" in content_type or "text/html" in content_type:
                            text = await response.text()
                            if len(text) > 4:
                                try:
                                    import json
                                    data = json.loads(text)
                                    found = extract_word_from_dict(data)
                                    if found:
                                        captured_correct_word = found
                                        logger.info(f"🎣 [XHR-JSON] Przechwycono słowo: '{captured_correct_word}'")
                                        return
                                except Exception:
                                    pass

                                # HTML fallback
                                match = re.search(r'id=["\'](?:comment_word|word)["\'][^>]*>([^<]+)<', text)
                                if match and match.group(1).strip():
                                    captured_correct_word = match.group(1).strip()
                                    logger.info(f"🎣 [XHR-HTML] Przechwycono słowo: '{captured_correct_word}'")
                except Exception:
                    pass

            page.on("response", handle_response)

            try:
                # 1. Logowanie do InstaLinga
                logger.info(f"🔑 Logowanie na konto: {login}...")
                await page.goto("https://instaling.pl/teacher.php?page=login", wait_until="domcontentloaded")
                await asyncio.sleep(1.0)

                # Odblokuj kliknięcia
                try:
                    await page.evaluate("""() => {
                        const sels = ['.fc-consent-root', '.fc-dialog-overlay', '.fc-dialog-container', '.modal-backdrop'];
                        sels.forEach(s => document.querySelectorAll(s).forEach(el => el.remove()));
                        document.body.style.overflow = 'auto';
                        document.body.style.pointerEvents = 'auto';
                    }""")
                except Exception:
                    pass

                # Wypełnij formularz logowania
                await page.fill("#log_email", login)
                await asyncio.sleep(0.3)
                await page.fill("#log_password", password)
                await asyncio.sleep(0.4)

                # Zatwierdź klawiszem Enter lub przyciskiem
                try:
                    btn = await page.query_selector("button[type='submit'], input[type='submit']")
                    if btn:
                        await btn.click(force=True)
                    else:
                        await page.press("#log_password", "Enter")
                except Exception:
                    pass

                # Czekaj na nawigację po logowaniu
                await page.wait_for_load_state("domcontentloaded")
                await asyncio.sleep(1.5)

                current_url = page.url
                if "page=login" in current_url or "Niepoprawny login" in await page.content():
                    result["message"] = "❌ Niepoprawny login lub hasło do InstaLinga."
                    logger.warning(f"Błąd logowania dla konta {login}")
                    await browser.close()
                    return result

                logger.info("✅ Zalogowano pomyślnie.")

                # 2. Panel ucznia - sprawdzenie stanu sesji
                content = await page.content()

                # Sprawdź czy dzisiejsza sesja została już wykonana (tylko gdy nie ma aktywnego przycisku rozpoczęcia/kontynuacji)
                has_start_btn = False
                candidate_links = await page.query_selector_all("a.btn-start-session, a[href*='app.php']")
                for cl in candidate_links:
                    cid = await cl.get_attribute("id") or ""
                    if "recover" not in cid.lower() and "plus" not in cid.lower():
                        has_start_btn = True
                        break

                if not has_start_btn and ("Sesja została już dzisiaj wykonana" in content or "Dzisiejsza sesja została już wykonana" in content):
                    result["success"] = True
                    result["already_done"] = True
                    result["message"] = "ℹ️ Dzisiejsza sesja na tym koncie została już wcześniej ukończona!"
                    result["time_seconds"] = round(time.time() - start_time, 1)
                    await browser.close()
                    return result

                # Wykryj język z panelu ucznia jeśli auto
                if preferred_lang == "auto":
                    content_lower = content.lower()
                    if "niemiecki" in content_lower or "język niemiecki" in content_lower:
                        current_detected_lang = "de"
                    elif "angielski" in content_lower or "język angielski" in content_lower:
                        current_detected_lang = "en"
                    result["language"] = current_detected_lang.upper()
                    logger.info(f"🎯 Automatycznie wykryty język: {result['language']}")

                # Znajdź właściwy link do sesji (omijając reklamy i bannery Instaling Plus)
                start_link = None
                for l in candidate_links:
                    href = await l.get_attribute("href") or ""
                    cid = await l.get_attribute("id") or ""
                    if "recover" in cid.lower() or "plus" in cid.lower():
                        continue
                    if "app.php" in href:
                        start_link = l
                        break

                if start_link:
                    href = await start_link.get_attribute("href") or ""
                    if href.startswith("/"):
                        href = "https://instaling.pl" + href
                    logger.info(f"🔗 Przechodzę do sesji: {href}")
                    await page.goto(href, wait_until="domcontentloaded")
                    await asyncio.sleep(1.5)
                else:
                    # Awaryjnie znajdź child_id w treści
                    child_match = re.search(r"app\.php\?child_id=(\d+)", content)
                    if child_match:
                        session_url = f"https://instaling.pl/app/session/app.php?child_id={child_match.group(1)}"
                        logger.info(f"🔗 Przechodzę bezpośrednio do sesji: {session_url}")
                        await page.goto(session_url, wait_until="domcontentloaded")
                        await asyncio.sleep(1.5)
                    else:
                        result["message"] = "❌ Nie znaleziono przycisku rozpoczęcia ani kontynuacji sesji."
                        await browser.close()
                        return result

                # 3. Kliknięcie 'Kontynuuj sesję' lub 'Rozpocznij sesję' na stronie sesji
                cont_loc = page.locator("#continue_session_button:visible, #start_session_button:visible, #continue_session_page:visible .btn, #start_session_page:visible .btn")
                if await cont_loc.count() > 0:
                    logger.info("🎬 Klikam 'Kontynuuj sesję' / 'Rozpocznij sesję'...")
                    try:
                        await cont_loc.first.click()
                    except Exception:
                        pass
                    await page.evaluate("""() => {
                        const c = document.getElementById('continue_session_button') || document.getElementById('start_session_button');
                        if (c) c.click();
                    }""")
                    await asyncio.sleep(1.8)

                # 4. Pętla rozwiązywania słówek (Główny algorytm ze spokojnym, ludzkim tempem)
                logger.info("🚀 Rozpoczynam rozwiązywanie sesji (tempo naturalne, niewykrywalne)...")
                max_iterations = 200  # 15 slow words * ~8-10 cycles each = up to 150 iterations needed
                iterations = 0

                while iterations < max_iterations:
                    iterations += 1
                    await asyncio.sleep(0.4)

                    # A. Ponowne sprawdzenie ekranu startu/kontynuacji (jeśli pojawił się z opóźnieniem)
                    cont_check = page.locator("#continue_session_button:visible, #start_session_button:visible, #continue_session_page:visible .btn, #start_session_page:visible .btn")
                    if await cont_check.count() > 0:
                        logger.info("🎬 Ponownie klikam start / kontynuuj...")
                        try:
                            await cont_check.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const c = document.getElementById('continue_session_button') || document.getElementById('start_session_button');
                            if (c) c.click();
                        }""")
                        await asyncio.sleep(1.5)
                        continue

                    # B. Zamknij ewentualne modale/powiadomienia
                    try:
                        modals = await page.query_selector_all(".modal button, .popup button, button:has-text('Rozumiem'), button:has-text('Zgadzam'), button:has-text('OK'), button:has-text('Zamknij')")
                        for mb in modals:
                            if await mb.is_visible():
                                await mb.click()
                                await asyncio.sleep(0.3)
                    except Exception:
                        pass

                    # C. Sprawdź zakończenie sesji - TYLKO przez widoczność #finish_page w DOM!
                    # UWAGA: "Gratulacje" / "Koniec sesji" SA ZAWSZE w HTML strony (ukryty div),
                    # wiec sprawdzanie page.content() powodowaloby false-positive po 1 slowie!
                    is_finished = await page.locator("#finish_page:visible").count() > 0

                    if is_finished and (result["total_words"] > 0 or iterations > 8):
                        logger.info("🎉 Sesja została zakończona sukcesem!")
                        result["success"] = True
                        result["message"] = "✅ Sesja wykonana pomyślnie!"
                        break

                    # D. Obsługa przycisku "Pomiń" (#skip_word / #skip)
                    skip_loc = page.locator("#skip_word:visible, #skip:visible, #possible_word_page:visible #skip")
                    if await skip_loc.count() > 0:
                        logger.info("⏭️ Wykryto 'Pomiń'. Czekam chwilę po ludzku...")
                        await asyncio.sleep(random.uniform(1.0, 1.8))
                        await skip_loc.first.click()
                        await asyncio.sleep(0.8)
                        continue

                    # E. Obsługa nowego słówka - przycisk "Znam" (#know_new / #know_word)
                    know_loc = page.locator("#know_new:visible, #know_word:visible, #new_word_form:visible #know_new")
                    if await know_loc.count() > 0:
                        logger.info("🧠 Nowe słówko -> czekam i klikam 'ZNAM'...")
                        await asyncio.sleep(random.uniform(1.4, 2.4))
                        await know_loc.first.click()
                        await asyncio.sleep(0.8)
                        # Sprawdź czy po Znam pojawiło się Pomiń
                        skip_after = page.locator("#skip_word:visible, #skip:visible")
                        if await skip_after.count() > 0:
                            await skip_after.first.click()
                            await asyncio.sleep(0.5)
                        continue

                    # F. Obsługa przycisku "Następne" - klikamy faktyczny przycisk #next_word
                    next_loc = page.locator("#next_word:visible, .btn:has-text('Następne'):visible")
                    if await next_loc.count() > 0:
                        logger.info("Klikam 'Nastepne'...")
                        await asyncio.sleep(random.uniform(config.NEXT_WORD_DELAY_MIN, config.NEXT_WORD_DELAY_MAX) / 1000.0)
                        await next_loc.first.click()
                        await asyncio.sleep(0.8)
                        continue

                    # G. Obsługa powrotu z ekranu błędu (#comment_back_button)
                    comment_back = page.locator("#comment_back_button:visible")
                    if await comment_back.count() > 0:
                        logger.info("🔙 Powrót do nauki...")
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        await comment_back.first.click()
                        await asyncio.sleep(0.8)
                        continue

                    # H. Standardowe pytanie słówka
                    ans_loc = page.locator("#answer:visible")
                    chk_loc = page.locator("#check:visible, #check:visible .btn, .btn:has-text('Sprawdź'):visible")
                    trans_loc = page.locator("#question:visible .translation, #learning_page:visible .translation, .translation:visible")

                    if await ans_loc.count() > 0 and await chk_loc.count() > 0:
                        polish_word = ""
                        if await trans_loc.count() > 0:
                            polish_word = (await trans_loc.first.inner_text()).strip()

                        if polish_word and len(polish_word) < 120 and not any(k in polish_word.lower() for k in ["instaling", "menu", "wyloguj", "sprawdź"]):
                            logger.info(f"🇵🇱 Pytanie: '{polish_word}'")

                            # 1. Spokojny czas na zastanowienie się i przeczytanie pytania
                            think_sec = random.uniform(config.THINKING_DELAY_MIN, config.THINKING_DELAY_MAX) / 1000.0
                            logger.info(f"🤔 Zastanawiam się ({think_sec:.1f}s)...")
                            await asyncio.sleep(think_sec)

                            # 2. Pobierz tłumaczenie z Firebase cache
                            answer = self.firebase.get_answer(polish_word, current_detected_lang)
                            captured_correct_word = None

                            if answer:
                                logger.info(f"🎯 Znam z bazy ({current_detected_lang.upper()}): '{answer}'")
                                await self.human_type(page, "#answer", answer)
                                result["total_words"] += 1
                                result["correct_words"] += 1
                            else:
                                logger.info("❓ Nieznane słowo w bazie -> pozostawiam puste do nauki")
                                await page.fill("#answer", "")
                                result["total_words"] += 1

                            # 3. Spokojna pauza przed zatwierdzeniem
                            action_sec = random.uniform(config.ACTION_DELAY_MIN, config.ACTION_DELAY_MAX) / 1000.0
                            await asyncio.sleep(action_sec)

                            # 4. Kliknij "Sprawdź"
                            await chk_loc.first.click()
                            await page.evaluate("""() => {
                                const c = document.getElementById('check') || document.querySelector('#check .btn');
                                if (c) c.click();
                            }""")

                            # 5. Czekaj na odpowiedź sieciową (XHR) lub zmianę widoku
                            for _ in range(15):
                                await asyncio.sleep(0.1)
                                if captured_correct_word:
                                    break

                            # Awaryjna detekcja poprawki z DOM
                            if not captured_correct_word:
                                cw_loc = page.locator("#comment_word:visible, #word:visible")
                                if await cw_loc.count() > 0:
                                    cw_text = (await cw_loc.first.inner_text()).strip()
                                    if cw_text and len(cw_text) < 120:
                                        captured_correct_word = cw_text
                                        logger.info(f"🔬 [DOM] Pobrana poprawka: '{captured_correct_word}'")

                            # 6. Zapisz nowe słowo w Firebase jeśli się nauczyliśmy
                            if captured_correct_word:
                                main_key = polish_word.split(";")[0].split(",")[0].strip().lower()
                                if not self.firebase.looks_polish(captured_correct_word) and not self.firebase.is_junk(captured_correct_word):
                                    if not answer or answer.lower() != captured_correct_word.lower():
                                        saved = await self.firebase.save_word(main_key, captured_correct_word, current_detected_lang)
                                        if saved:
                                            result["learned_words"] += 1

                            # 7. Ludzki czas na obejrzenie wyniku
                            if answer:
                                view_sec = random.uniform(config.NEXT_WORD_DELAY_MIN, config.NEXT_WORD_DELAY_MAX) / 1000.0
                            else:
                                view_sec = random.uniform(2.5, 4.0)
                            await asyncio.sleep(view_sec)

                            # 8. Przejdź do następnego pytania (klikamy faktyczny przycisk #next_word, nie kontener)
                            nxt_after = page.locator("#next_word:visible, .btn:has-text('Następne'):visible, #comment_back_button:visible")
                            if await nxt_after.count() > 0:
                                await nxt_after.first.click()
                            else:
                                # Kliknij Enter jako fallback
                                try:
                                    await page.keyboard.press("Enter")
                                except Exception:
                                    pass

                            await asyncio.sleep(0.5)
                            continue

                    # Jeśli nic nie pasuje w tym cyklu, odczekaj krótką chwilę
                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Wyjątek podczas rozwiązywania sesji: {e}", exc_info=True)
                result["message"] = f"❌ Wystąpił błąd: {str(e)}"
            finally:
                result["time_seconds"] = round(time.time() - start_time, 1)
                await browser.close()

        return result
