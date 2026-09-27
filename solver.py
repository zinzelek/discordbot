import asyncio
import logging
import random
import re
import time
from typing import Dict, Any, Optional
from playwright.async_api import async_playwright, Page, Response

import config
from firebase_sync import FirebaseClient

logger = logging.getLogger("InstaLingBot.Solver")

class InstaLingSolver:
    def __init__(self, firebase_client: FirebaseClient, headless: bool = True):
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
        """
        Loguje się na konto InstaLing i automatycznie rozwiązuje sesję słówek.
        W 100% niewykrywalne, spokojne tempo emulujące prawdziwego ucznia.
        """
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

        async with async_playwright() as p:
            # Uruchomienie przeglądarki Chromium
            browser = await p.chromium.launch(
                headless=self.headless,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-dev-shm-usage",
                    "--disable-accelerated-2d-canvas",
                    "--disable-gpu",
                    "--no-first-run",
                    "--no-zygote",
                    "--disable-blink-features=AutomationControlled"
                ]
            )

            # Konfiguracja kontekstu o parametrach typowego użytkownika Windows / Chrome
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="pl-PL"
            )

            # Ukryj flagę navigator.webdriver oraz natychmiast usuwaj overlaye reklamowe
            await context.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
                const cleanOverlays = () => {
                    const selectors = [
                        '.fc-consent-root', '.fc-dialog-overlay', '.fc-dialog-container',
                        '#credential_picker_container', '#google_esf', 'iframe[src*="google"]',
                        'iframe[src*="fundingchoices"]', 'div[class*="consent"]', 'div[class*="overlay"]'
                    ];
                    for (const s of selectors) {
                        try {
                            const els = document.querySelectorAll(s);
                            els.forEach(el => el.remove());
                        } catch (e) {}
                    }
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
            captured_correct_word = None

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
                    if any(k in url for k in ["app.php", "check", "action", "learning", "session", "word"]):
                        content_type = response.headers.get("content-type", "").lower()
                        if "application/json" in content_type or "text/html" in content_type or "text/plain" in content_type:
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

                                # HTML / regex fallback dla DE, EN, PL
                                match = re.search(r'id=["\'](?:comment_word|word)["\'][^>]*>([^<]+)<', text)
                                if match and match.group(1).strip():
                                    captured_correct_word = match.group(1).strip()
                                    logger.info(f"🎣 [XHR-HTML] Przechwycono słowo: '{captured_correct_word}'")
                                    return
                                match_de = re.search(r'(?:poprawna odpowiedź|richtige antwort|correct answer)\s*[:：]\s*([^\n\r<]+)', text, re.I)
                                if match_de and match_de.group(1).strip():
                                    captured_correct_word = match_de.group(1).strip()
                                    logger.info(f"🎣 [XHR-REGEX] Przechwycono słowo: '{captured_correct_word}'")
                except Exception:
                    pass

            page.on("response", handle_response)

            try:
                # 1. Logowanie na InstaLing
                logger.info(f"🔑 Logowanie na konto: {login}...")
                await page.goto("https://instaling.pl/teacher.php?page=login", wait_until="domcontentloaded", timeout=30000)
                await asyncio.sleep(1.0)

                # Wpisz dane logowania
                await page.fill("#log_email", login)
                await asyncio.sleep(random.uniform(0.2, 0.4))
                await page.fill("#log_password", password)
                await asyncio.sleep(random.uniform(0.3, 0.6))

                # Kliknij ZALOGUJ bezpośrednio przez JS (omija wszelkie niewidoczne overlaye)
                await page.evaluate("""() => {
                    const btn = document.querySelector('button[type="submit"]') || document.querySelector('.btn-primary');
                    if (btn) btn.click();
                }""")

                # Poczekaj na załadowanie panelu ucznia
                try:
                    await page.wait_for_url("**/student/**", timeout=12000)
                except Exception:
                    # Sprawdź czy pojawił się komunikat o błędnym haśle/loginie
                    content = await page.content()
                    if "Niepoprawny e-mail lub hasło" in content or "Błędny login" in content:
                        result["message"] = "❌ Niepoprawny login lub hasło do InstaLinga!"
                        await browser.close()
                        return result

                logger.info("✅ Zalogowano pomyślnie.")
                await asyncio.sleep(1.2)

                # 2. Sprawdzenie czy sesja jest dostępna / czy nie została już zrobiona
                content = await page.content()

                # Znajdź przycisk sesji
                session_link = None
                candidate_links = await page.query_selector_all("a.btn-start-session, a.btn-make-up-session-light, a[href*='app.php']")
                for cl in candidate_links:
                    href = await cl.get_attribute("href") or ""
                    cid = await cl.get_attribute("id") or ""
                    if href and "app.php" in href and "recover" not in cid.lower() and "plus" not in cid.lower():
                        session_link = href
                        break

                has_start_btn = False
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
                    else:
                        current_detected_lang = "en"
                else:
                    current_detected_lang = preferred_lang.lower().strip()

                result["language"] = current_detected_lang.upper()

                # 3. Wejście w sesję (app.php)
                if session_link:
                    if session_link.startswith("/"):
                        full_session_url = "https://instaling.pl" + session_link
                    elif not session_link.startswith("http"):
                        full_session_url = "https://instaling.pl/student/pages/" + session_link
                    else:
                        full_session_url = session_link
                    logger.info(f"🔗 Przechodzę do sesji: {full_session_url}")
                    await page.goto(full_session_url, wait_until="domcontentloaded", timeout=25000)
                else:
                    logger.info("🎬 Szukam przycisku sesji na stronie...")
                    btn_clicked = await page.evaluate("""() => {
                        const b = document.querySelector('a.btn-start-session') || document.querySelector('a[href*="app.php"]');
                        if (b) { b.click(); return true; }
                        return false;
                    }""")
                    if not btn_clicked:
                        result["message"] = "ℹ️ Brak aktywnej sesji na dziś (być może już wykonana)!"
                        result["already_done"] = True
                        result["success"] = True
                        await browser.close()
                        return result

                await asyncio.sleep(2.0)

                # Obsługa ekranu startowego / kontynuacji sesji
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
                max_iterations = 220
                iterations = 0
                word_attempts: Dict[str, int] = {}

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
                    is_finished = await page.locator("#finish_page:visible").count() > 0

                    if is_finished and (result["total_words"] > 0 or iterations > 8):
                        logger.info("🎉 Sesja została zakończona sukcesem!")
                        result["success"] = True
                        result["message"] = "✅ Sesja wykonana pomyślnie!"
                        break

                    # D. Obsługa przycisku "Pomiń" (#skip_word / #skip) - PL, DE, EN
                    skip_loc = page.locator("#skip_word:visible, #skip:visible, #possible_word_page:visible #skip, .btn:has-text('Pomiń'):visible, .btn:has-text('Überspringen'):visible, .btn:has-text('Skip'):visible")
                    if await skip_loc.count() > 0:
                        logger.info("⏭️ Wykryto 'Pomiń'. Czekam chwilę po ludzku...")
                        await asyncio.sleep(random.uniform(1.0, 1.8))
                        try:
                            await skip_loc.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const s = document.getElementById('skip_word') || document.getElementById('skip');
                            if (s) s.click();
                        }""")
                        await asyncio.sleep(0.8)
                        continue

                    # E. Obsługa nowego słówka - przycisk "Znam" (#know_new / #know_word) - PL, DE, EN
                    know_loc = page.locator("#know_new:visible, #know_word:visible, #new_word_form:visible #know_new, .btn:has-text('Znam'):visible, .btn:has-text('Ich weiß'):visible, .btn:has-text('I know'):visible")
                    if await know_loc.count() > 0:
                        logger.info("🧠 Nowe słówko -> czekam i klikam 'ZNAM'...")
                        await asyncio.sleep(random.uniform(1.4, 2.4))
                        try:
                            await know_loc.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const k = document.getElementById('know_new') || document.getElementById('know_word');
                            if (k) k.click();
                        }""")
                        await asyncio.sleep(0.8)
                        # Sprawdź czy po Znam pojawiło się Pomiń
                        skip_after = page.locator("#skip_word:visible, #skip:visible")
                        if await skip_after.count() > 0:
                            await skip_after.first.click()
                            await asyncio.sleep(0.5)
                        continue

                    # F. Obsługa przycisku "Następne" / "Weiter" / "Next"
                    next_loc = page.locator("#next_word:visible, #nextword:visible, #nextword:visible .btn, .btn:has-text('Następne'):visible, .btn:has-text('Weiter'):visible, .btn:has-text('Next'):visible")
                    if await next_loc.count() > 0:
                        logger.info("➡️ Klikam 'Następne'...")
                        await asyncio.sleep(random.uniform(config.NEXT_WORD_DELAY_MIN, config.NEXT_WORD_DELAY_MAX) / 1000.0)
                        try:
                            await next_loc.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const n = document.getElementById('next_word') || document.getElementById('nextword');
                            if (n) n.click();
                        }""")
                        await asyncio.sleep(0.8)
                        continue

                    # G. Obsługa powrotu z ekranu błędu (#comment_back_button)
                    comment_back = page.locator("#comment_back_button:visible, #comment_page:visible .btn")
                    if await comment_back.count() > 0:
                        logger.info("🔙 Powrót do nauki z ekranu poprawki...")
                        await asyncio.sleep(random.uniform(2.0, 3.5))
                        try:
                            await comment_back.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const cb = document.getElementById('comment_back_button') || document.querySelector('#comment_page .btn');
                            if (cb) cb.click();
                        }""")
                        await asyncio.sleep(0.8)
                        continue

                    # H. Standardowe pytanie słówka
                    ans_loc = page.locator("#answer:visible")
                    chk_loc = page.locator("#check:visible, #check:visible .btn, .btn:has-text('Sprawdź'):visible, .btn:has-text('Prüfen'):visible, .btn:has-text('Check'):visible")
                    trans_loc = page.locator("#question:visible .translation, #learning_page:visible .translation, .translation:visible")

                    if await ans_loc.count() > 0 and await chk_loc.count() > 0:
                        polish_word = ""
                        if await trans_loc.count() > 0:
                            polish_word = (await trans_loc.first.inner_text()).strip()

                        if polish_word and len(polish_word) < 120 and not any(k in polish_word.lower() for k in ["instaling", "menu", "wyloguj", "sprawdź", "prüfen", "check"]):
                            logger.info(f"🇵🇱 Pytanie: '{polish_word}'")

                            # Śledzenie prób odpowiedzi na to samo słowo
                            norm_pl = polish_word.lower().strip()
                            word_attempts[norm_pl] = word_attempts.get(norm_pl, 0) + 1

                            # 1. Spokojny czas na zastanowienie się i przeczytanie pytania
                            think_sec = random.uniform(config.THINKING_DELAY_MIN, config.THINKING_DELAY_MAX) / 1000.0
                            logger.info(f"🤔 Zastanawiam się ({think_sec:.1f}s)...")
                            await asyncio.sleep(think_sec)

                            # 2. Pobierz tłumaczenie z Firebase cache
                            answer = self.firebase.get_answer(polish_word, current_detected_lang)
                            captured_correct_word = None

                            # ZABEZPIECZENIE PRZED ZAPĘTLENIEM (Anti-Loop):
                            # Jeśli słowo powtarza się 2. lub kolejny raz, to znaczy że stara odpowiedź w bazie była błędna!
                            if word_attempts[norm_pl] >= 2:
                                logger.warning(f"⚠️ Słowo '{polish_word}' wystąpiło po raz {word_attempts[norm_pl]}! Poprzednia odpowiedź ('{answer}') mogła być nieprawidłowa. Czyszczę cache i zostawiam puste, aby wymusić pobranie poprawki!")
                                main_key = norm_pl.split(";")[0].split(",")[0].strip()
                                safe_key = self.firebase.sanitize_key(main_key)
                                self.firebase.cache.get(current_detected_lang, {}).pop(main_key, None)
                                self.firebase.cache.get(current_detected_lang, {}).pop(safe_key, None)
                                answer = None

                            if answer:
                                logger.info(f"🎯 Znam z bazy ({current_detected_lang.upper()}): '{answer}'")
                                await self.human_type(page, "#answer", answer)
                                result["total_words"] += 1
                                result["correct_words"] += 1
                            else:
                                logger.info("❓ Nieznane słowo / pobieranie poprawki -> pozostawiam puste")
                                await page.fill("#answer", "")
                                result["total_words"] += 1

                            # 3. Spokojna pauza przed zatwierdzeniem
                            action_sec = random.uniform(config.ACTION_DELAY_MIN, config.ACTION_DELAY_MAX) / 1000.0
                            await asyncio.sleep(action_sec)

                            # 4. Kliknij "Sprawdź" / "Prüfen"
                            try:
                                await chk_loc.first.click()
                            except Exception:
                                pass
                            await page.evaluate("""() => {
                                const c = document.getElementById('check') || document.querySelector('#check .btn') || document.querySelector('.btn[type="submit"]');
                                if (c) c.click();
                            }""")

                            # 5. Czekaj na odpowiedź sieciową (XHR) lub zmianę widoku (do 2.5s)
                            for _ in range(25):
                                await asyncio.sleep(0.1)
                                if captured_correct_word:
                                    break

                            # Awaryjna detekcja poprawki z DOM (dla DE, EN, PL)
                            if not captured_correct_word:
                                dom_captured = await page.evaluate(r"""() => {
                                    const cp = document.getElementById('comment_page') || document.getElementById('answer_page');
                                    if (cp && cp.offsetParent !== null) {
                                        const text = (cp.innerText || cp.textContent || '').trim();
                                        const match = text.match(/(?:poprawna odpowiedź|richtige antwort|correct answer)\s*[:：]\s*([^\n\r]+)/i);
                                        if (match && match[1]) return match[1].trim();
                                    }
                                    const cw = document.getElementById('comment_word') || document.getElementById('word');
                                    if (cw && cw.offsetParent !== null) return (cw.innerText || cw.textContent || '').trim();
                                    return null;
                                }""")
                                if dom_captured and len(dom_captured) < 120 and not self.firebase.looks_polish(dom_captured) and not self.firebase.is_junk(dom_captured):
                                    captured_correct_word = dom_captured
                                    logger.info(f"🔬 [DOM] Pobrana poprawka: '{captured_correct_word}'")

                            # 6. Zapisz nowe słowo w pamięci lokalnej i w Firebase
                            if captured_correct_word:
                                main_key = norm_pl.split(";")[0].split(",")[0].strip()
                                if not self.firebase.looks_polish(captured_correct_word) and not self.firebase.is_junk(captured_correct_word):
                                    # Zawsze natychmiast uaktualnij pamięć podręczną na czas obecnej sesji:
                                    if current_detected_lang not in self.firebase.cache:
                                        self.firebase.cache[current_detected_lang] = {}
                                    self.firebase.cache[current_detected_lang][main_key] = captured_correct_word

                                    if not answer or answer.lower() != captured_correct_word.lower():
                                        saved = await self.firebase.save_word(main_key, captured_correct_word, current_detected_lang)
                                        if saved:
                                            result["learned_words"] += 1

                            # 7. Czas na obejrzenie wyniku
                            if answer:
                                view_sec = random.uniform(config.NEXT_WORD_DELAY_MIN, config.NEXT_WORD_DELAY_MAX) / 1000.0
                            else:
                                view_sec = random.uniform(2.5, 4.0)
                            await asyncio.sleep(view_sec)

                            # 8. Przejdź do następnego pytania
                            nxt_after = page.locator("#next_word:visible, #nextword:visible, #nextword:visible .btn, .btn:has-text('Następne'):visible, .btn:has-text('Weiter'):visible, .btn:has-text('Next'):visible, #comment_back_button:visible")
                            if await nxt_after.count() > 0:
                                try:
                                    await nxt_after.first.click()
                                except Exception:
                                    pass

                            await page.evaluate("""() => {
                                const nb = document.getElementById('next_word') || document.getElementById('nextword') || document.getElementById('comment_back_button');
                                if (nb && nb.offsetParent !== null) nb.click();
                            }""")

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
