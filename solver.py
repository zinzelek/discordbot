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
            await asyncio.sleep(random.uniform(0.1, 0.2))
            await page.fill(selector, "")
            await asyncio.sleep(random.uniform(0.05, 0.1))
            for char in text:
                delay_ms = random.randint(config.TYPING_SPEED_MIN, config.TYPING_SPEED_MAX)
                if char in (" ", "-", "'"):
                    delay_ms += random.randint(50, 120)
                await page.type(selector, char, delay=delay_ms)
                if random.random() < 0.08:
                    await asyncio.sleep(random.uniform(0.12, 0.28))
                else:
                    await asyncio.sleep(random.uniform(0.01, 0.03))
            
            # Wymuś zdarzenia input i change
            await page.evaluate(f"""() => {{
                const el = document.querySelector('{selector}');
                if (el) {{
                    el.dispatchEvent(new Event('input', {{ bubbles: true }}));
                    el.dispatchEvent(new Event('change', {{ bubbles: true }}));
                }}
            }}""")
        except Exception as e:
            logger.warning(f"human_type fallback: {e}")
            await page.fill(selector, text)

    async def safe_get_content(self, page: Page, max_retries: int = 6) -> str:
        """Pobiera zawartość strony bez ryzyka błędu 'Unable to retrieve content because the page is navigating'"""
        for attempt in range(max_retries):
            try:
                return await page.content()
            except Exception as e:
                err_str = str(e).lower()
                if "navigating" in err_str:
                    try:
                        await page.wait_for_load_state("domcontentloaded", timeout=4000)
                    except Exception:
                        pass
                    await asyncio.sleep(1.0)
                else:
                    await asyncio.sleep(0.5)
        try:
            return await page.content()
        except Exception:
            return ""

    async def dismiss_overlays(self, page: Page):
        """Usuwa modale, cookie consent i odblokowuje body"""
        try:
            await page.evaluate("""() => {
                const consentBtns = document.querySelectorAll(
                    '.fc-cta-consent, .fc-primary-button, button[aria-label="Consent"], ' +
                    '.fc-button-background, .fc-button[data-testid="consent"], ' +
                    'button.fc-cta-consent, .fc-dialog .fc-footer .fc-button, ' +
                    'button:has-text("Zgadzam się"), button:has-text("Consent"), button:has-text("Rozumiem")'
                );
                for (const b of consentBtns) {
                    try { if (b.offsetParent !== null) b.click(); } catch(e) {}
                }
                const overlays = document.querySelectorAll(
                    '.fc-consent-root, .fc-dialog-overlay, .fc-dialog-container, ' +
                    '#credential_picker_container, iframe[src*="google"], ' +
                    'iframe[src*="fundingchoices"], div[class*="consent"], div[class*="overlay"]'
                );
                for (const o of overlays) {
                    try { o.remove(); } catch(e) {}
                }
                if (document.body) {
                    document.body.style.overflow = 'auto';
                    document.body.style.pointerEvents = 'auto';
                }
            }""")
        except Exception:
            pass

    async def solve_session(self, login: str, password: str, preferred_lang: str = "auto") -> Dict[str, Any]:
        """
        Loguje się na konto InstaLing i automatycznie rozwiązuje całą sesję słówek.
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

            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
                locale="pl-PL"
            )

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

            # Blokuj zbędne pliki audio (.mp3) i skrypty reklamowe
            await page.route("**/*.mp3", lambda route: route.abort())
            await page.route("**/*fundingchoices*", lambda route: route.abort())

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
                for _goto_attempt in range(3):
                    try:
                        await page.goto("https://instaling.pl/teacher.php?page=login", wait_until="domcontentloaded", timeout=35000)
                        break
                    except Exception as goto_err:
                        if _goto_attempt < 2:
                            logger.warning(f"⚠️ Timeout ładowania strony (próba {_goto_attempt + 1}/3): {goto_err}")
                            await asyncio.sleep(2)
                        else:
                            raise goto_err

                await asyncio.sleep(1.0)
                await self.dismiss_overlays(page)

                # Sprawdź czy strona logowania jest załadowana lub czy jesteśmy już zalogowani
                logged_in_already = False
                email_found = False
                for _find_attempt in range(3):
                    if "student" in page.url.lower() or "app.php" in page.url.lower():
                        logged_in_already = True
                        break
                    await self.dismiss_overlays(page)
                    try:
                        email_el = await page.wait_for_selector(
                            "#log_email, input[name='log_email'], input[autocomplete='username']",
                            timeout=7000,
                            state="attached"
                        )
                        if email_el:
                            email_found = True
                            break
                    except Exception:
                        logger.warning(f"⚠️ Nie znaleziono pola logowania (próba {_find_attempt + 1}/3), odświeżam stronę...")
                        try:
                            await page.goto("https://instaling.pl/teacher.php?page=login", wait_until="domcontentloaded", timeout=25000)
                        except Exception:
                            pass
                        await asyncio.sleep(1.5)

                if not logged_in_already and not email_found:
                    if "student" in page.url.lower() or "app.php" in page.url.lower():
                        logged_in_already = True
                    else:
                        result["message"] = f"❌ Nie udało się załadować formularza logowania (URL: {page.url})"
                        await browser.close()
                        return result

                if not logged_in_already:
                    await self.dismiss_overlays(page)
                    # Wypełnij login i hasło (bezpiecznie z force=True i evaluate fallback)
                    try:
                        await page.fill("#log_email", login, force=True, timeout=5000)
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(0.15, 0.3))

                    try:
                        await page.fill("#log_password", password, force=True, timeout=5000)
                    except Exception:
                        pass
                    await asyncio.sleep(random.uniform(0.2, 0.4))

                    # Upewnij się, że pola są faktycznie wypełnione i zdarzenia wysłane
                    await page.evaluate("""([l, p]) => {
                        const emailInput = document.querySelector('#log_email') || document.querySelector('input[name="log_email"]');
                        const passInput = document.querySelector('#log_password') || document.querySelector('input[name="log_password"]');
                        if (emailInput) {
                            emailInput.value = l;
                            emailInput.dispatchEvent(new Event('input', { bubbles: true }));
                            emailInput.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                        if (passInput) {
                            passInput.value = p;
                            passInput.dispatchEvent(new Event('input', { bubbles: true }));
                            passInput.dispatchEvent(new Event('change', { bubbles: true }));
                        }
                    }""", [login, password])

                    # Kliknij Zaloguj
                    await page.evaluate("""() => {
                        const btn = document.querySelector('button[type="submit"]') || document.querySelector('.btn-primary');
                        if (btn) btn.click();
                    }""")

                    # Czekaj na przejście do panelu studenta lub błąd logowania (do 25s)
                    login_ok = False
                    for _wait in range(25):
                        cur_url = page.url.lower()
                        if "student" in cur_url or "app.php" in cur_url:
                            login_ok = True
                            break

                        # Sprawdź widoczne komunikaty błędów
                        try:
                            err_el = await page.query_selector(".alert-danger, #login_error, .error, #log_email_error")
                            if err_el and await err_el.is_visible():
                                err_txt = await err_el.inner_text()
                                if any(w in err_txt.lower() for w in ["niepoprawny", "błędny", "hasło", "login"]):
                                    result["message"] = f"❌ Niepoprawny login lub hasło do InstaLinga ({err_txt.strip()})"
                                    await browser.close()
                                    return result
                        except Exception:
                            pass

                        await asyncio.sleep(1.0)

                    if not login_ok:
                        content = await self.safe_get_content(page)
                        if "Niepoprawny e-mail lub hasło" in content or "Błędny login" in content:
                            result["message"] = "❌ Niepoprawny login lub hasło do InstaLinga!"
                            await browser.close()
                            return result
                        elif "student" in page.url.lower():
                            login_ok = True
                        else:
                            result["message"] = f"❌ Logowanie przekroczyło limit czasu (URL: {page.url})"
                            await browser.close()
                            return result

                logger.info("✅ Zalogowano pomyślnie.")
                await asyncio.sleep(1.2)

                # 2. Sprawdzenie stanu sesji
                content = await self.safe_get_content(page)
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

                # Wykryj język
                if preferred_lang == "auto":
                    content_lower = content.lower()
                    if "niemiecki" in content_lower or "język niemiecki" in content_lower:
                        current_detected_lang = "de"
                    else:
                        current_detected_lang = "en"
                else:
                    current_detected_lang = preferred_lang.lower().strip()

                result["language"] = current_detected_lang.upper()

                # 3. Wejście w sesję
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

                # Ekran startowy / kontynuacji sesji
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

                # 4. Główna pętla rozwiązywania
                logger.info("🚀 Rozpoczynam rozwiązywanie sesji (tempo naturalne, niewykrywalne)...")
                max_iterations = 220
                iterations = 0
                last_processed_word = ""
                word_repeat_count: Dict[str, int] = {}
                failed_answers_per_word: Dict[str, set] = {}  # norm_pl -> zbiór błędnych odpowiedzi w tej sesji
                dots_typed_per_word: Dict[str, int] = {}  # norm_pl -> ile razy wpisano '.' w tej sesji
                last_progress_iter = 0  # ostatnia iteracja, w której coś się wydarzyło
                stall_logged = False

                while iterations < max_iterations:
                    iterations += 1
                    await asyncio.sleep(0.4)

                    # A. Ponowne sprawdzenie ekranu startu/kontynuacji
                    cont_check = page.locator("#continue_session_button:visible, #start_session_button:visible, #continue_session_page:visible .btn, #start_session_page:visible .btn")
                    if await cont_check.count() > 0:
                        try:
                            await cont_check.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const c = document.getElementById('continue_session_button') || document.getElementById('start_session_button');
                            if (c) c.click();
                        }""")
                        await asyncio.sleep(1.5)
                        last_progress_iter = iterations
                        stall_logged = False
                        continue

                    # B. Zamknij ewentualne modale
                    try:
                        modals = await page.query_selector_all(".modal button, .popup button, button:has-text('Rozumiem'), button:has-text('Zgadzam'), button:has-text('OK'), button:has-text('Zamknij')")
                        for mb in modals:
                            if await mb.is_visible():
                                await mb.click()
                                await asyncio.sleep(0.3)
                    except Exception:
                        pass

                    # C. Sprawdź zakończenie sesji
                    if await page.locator("#finish_page:visible").count() > 0:
                        logger.info("🎉 Sesja została zakończona sukcesem!")
                        result["success"] = True
                        result["message"] = "✅ Sesja wykonana pomyślnie!"
                        break

                    # D. Obsługa ekranu wyniku (#answer_page) — "Dobrze!" lub "Źle!"
                    answer_page_loc = page.locator("#answer_page:visible")
                    if await answer_page_loc.count() > 0:
                        # Spróbuj odczytać poprawną odpowiedź z answer_page (w przypadku błędu)
                        dom_captured = await page.evaluate(r"""() => {
                            const ap = document.getElementById('answer_page');
                            if (!ap || ap.offsetParent === null) return null;
                            // Sprawdź #comment_word (pojawia się przy złej odpowiedzi)
                            const cw = ap.querySelector('#comment_word') || document.getElementById('comment_word');
                            if (cw && cw.offsetParent !== null) {
                                const val = (cw.innerText || cw.textContent || '').trim();
                                if (val && val.length > 0 && val.length < 120) return {word: val, wrong: true};
                            }
                            // Sprawdź regex w tekście
                            const text = (ap.innerText || ap.textContent || '').trim();
                            const match = text.match(/(?:poprawna odpowiedź|richtige antwort|correct answer)\s*[:：]\s*([^\n\r<]+)/i);
                            if (match && match[1]) return {word: match[1].trim(), wrong: true};
                            // Sprawdź wynik
                            const result = ap.querySelector('#answer_result');
                            if (result) {
                                const rt = (result.innerText || '').trim().toLowerCase();
                                if (rt.includes('dobrze') || rt.includes('richtig') || rt.includes('correct')) {
                                    return {word: null, wrong: false};
                                }
                            }
                            return {word: null, wrong: false};
                        }""")
                        if dom_captured and dom_captured.get("wrong") and dom_captured.get("word") and last_processed_word:
                            clean_cw = dom_captured["word"].strip()
                            if not self.firebase.looks_polish(clean_cw) and not self.firebase.is_junk(clean_cw):
                                if last_processed_word in failed_answers_per_word:
                                    failed_answers_per_word[last_processed_word].discard(clean_cw.lower())
                                parts = [p.strip() for p in re.split(r"[,;]", last_processed_word) if p.strip()]
                                for part in parts:
                                    self.firebase.cache.get(current_detected_lang, {})[part] = clean_cw
                                main_k = parts[0] if parts else last_processed_word
                                saved = await self.firebase.save_word(main_k, clean_cw, current_detected_lang)
                                if saved:
                                    result["learned_words"] += 1
                                logger.info(f"💾 Odczytano z answer_page i zapisano: '{main_k}' = '{clean_cw}'")

                        # Kliknij "Następne" na answer_page
                        nxt_btn = page.locator("#answer_page:visible #next_word, #answer_page:visible #nextword, #next_word:visible, #nextword:visible")
                        if await nxt_btn.count() > 0:
                            await asyncio.sleep(random.uniform(1.0, 2.0))
                            try:
                                await nxt_btn.first.click()
                            except Exception:
                                pass
                        await page.evaluate("""() => {
                            const n = document.getElementById('next_word') || document.getElementById('nextword');
                            if (n && n.offsetParent !== null) n.click();
                        }""")
                        await asyncio.sleep(0.8)
                        last_progress_iter = iterations
                        stall_logged = False
                        continue

                    # E. Obsługa ekranu poprawki / błędu (#comment_page / #comment_answers)
                    comment_loc = page.locator("#comment_page:visible, #comment_answers:visible")
                    if await comment_loc.count() > 0:
                        dom_captured = await page.evaluate(r"""() => {
                            const cp = document.getElementById('comment_page') || document.getElementById('answer_page');
                            if (cp && cp.offsetParent !== null) {
                                const text = (cp.innerText || cp.textContent || '').trim();
                                const match = text.match(/(?:poprawna odpowiedź|richtige antwort|correct answer)\s*[:：]\s*([^\n\r<]+)/i);
                                if (match && match[1]) return match[1].trim();
                            }
                            const cw = document.getElementById('comment_word') || document.getElementById('word');
                            if (cw && cw.offsetParent !== null) return (cw.innerText || cw.textContent || '').trim();
                            return null;
                        }""")
                        if dom_captured and last_processed_word:
                            clean_cw = dom_captured.strip()
                            if not self.firebase.looks_polish(clean_cw) and not self.firebase.is_junk(clean_cw):
                                if last_processed_word in failed_answers_per_word:
                                    failed_answers_per_word[last_processed_word].discard(clean_cw.lower())
                                parts = [p.strip() for p in re.split(r"[,;]", last_processed_word) if p.strip()]
                                for part in parts:
                                    self.firebase.cache.get(current_detected_lang, {})[part] = clean_cw
                                main_k = parts[0] if parts else last_processed_word
                                saved = await self.firebase.save_word(main_k, clean_cw, current_detected_lang)
                                if saved:
                                    result["learned_words"] += 1
                                logger.info(f"💾 Odczytano z ekranu błędu i zapisano: '{main_k}' = '{clean_cw}'")

                        # Kliknij przycisk powrotu lub Enter
                        cb_btn = page.locator("#comment_back_button:visible, #comment_page:visible #next_word, #comment_page:visible .btn, .btn:has-text('Powrót'):visible, .btn:has-text('Dalej'):visible, .btn:has-text('Zurück'):visible, .btn:has-text('Weiter'):visible")
                        if await cb_btn.count() > 0:
                            try:
                                await cb_btn.first.click()
                            except Exception:
                                pass
                        else:
                            await page.keyboard.press("Enter")
                        await asyncio.sleep(1.0)
                        last_progress_iter = iterations
                        stall_logged = False
                        continue

                    # F. Obsługa przycisku "Pomiń" (#skip_word / #skip)
                    skip_loc = page.locator("#skip_word:visible, #skip:visible, #possible_word_page:visible #skip, .btn:has-text('Pomiń'):visible, .btn:has-text('Überspringen'):visible, .btn:has-text('Skip'):visible")
                    if await skip_loc.count() > 0:
                        logger.info("⏭️ Wykryto 'Pomiń'. Czekam chwilę...")
                        await asyncio.sleep(random.uniform(1.0, 1.8))
                        try:
                            await skip_loc.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const s = document.getElementById('skip_word') || document.getElementById('skip');
                            if (s && s.offsetParent !== null) s.click();
                        }""")
                        await asyncio.sleep(0.8)
                        last_progress_iter = iterations
                        stall_logged = False
                        continue

                    # G. Obsługa nowego słówka - "Znam" (#know_new / #know_word)
                    know_loc = page.locator("#know_new:visible, #know_word:visible, #new_word_form:visible #know_new, .btn:has-text('Znam'):visible, .btn:has-text('Ich weiß'):visible, .btn:has-text('I know'):visible")
                    if await know_loc.count() > 0:
                        logger.info("🧠 Nowe słówko -> klikam 'ZNAM'...")
                        await asyncio.sleep(random.uniform(1.4, 2.2))
                        try:
                            await know_loc.first.click()
                        except Exception:
                            pass
                        await page.evaluate("""() => {
                            const k = document.getElementById('know_new') || document.getElementById('know_word');
                            if (k && k.offsetParent !== null) k.click();
                        }""")
                        await asyncio.sleep(0.8)
                        skip_after = page.locator("#skip_word:visible, #skip:visible")
                        if await skip_after.count() > 0:
                            try:
                                await skip_after.first.click()
                            except Exception:
                                pass
                            await asyncio.sleep(0.5)
                        last_progress_iter = iterations
                        stall_logged = False
                        continue

                    # H. Obsługa przycisku "Następne" / "Weiter"
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
                            if (n && n.offsetParent !== null) n.click();
                        }""")
                        await asyncio.sleep(0.8)
                        last_progress_iter = iterations
                        stall_logged = False
                        continue

                    # I. Standardowe pytanie słówka (#learning_page)
                    ans_loc = page.locator("#learning_page:visible #answer, #answer:visible")
                    chk_loc = page.locator("#learning_page:visible #check, #check:visible, #check:visible .btn, .btn:has-text('Sprawdź'):visible, .btn:has-text('Prüfen'):visible, .btn:has-text('Check'):visible")
                    trans_loc = page.locator("#question:visible .translations, #question:visible .translation, #learning_page:visible .translation, .translation:visible")

                    if await ans_loc.count() > 0 and await chk_loc.count() > 0:
                        polish_word = ""
                        if await trans_loc.count() > 0:
                            polish_word = (await trans_loc.first.inner_text()).strip()

                        if polish_word and len(polish_word) < 120 and not any(k in polish_word.lower() for k in ["instaling", "menu", "wyloguj", "sprawdź", "prüfen", "check"]):
                            norm_pl = polish_word.lower().strip()

                            # Sprawdź czy to nowe pytanie czy jeszcze trwa poprzednie
                            is_new_question = (norm_pl != last_processed_word)
                            if not is_new_question:
                                ans_val = await page.evaluate("() => { const a = document.getElementById('answer'); return a ? a.value : null; }")
                                if ans_val == "":
                                    is_new_question = True

                            if is_new_question:
                                last_processed_word = norm_pl
                                word_repeat_count[norm_pl] = word_repeat_count.get(norm_pl, 0) + 1
                                logger.info(f"🇵🇱 Pytanie: '{polish_word}' (wystąpienie: {word_repeat_count[norm_pl]})")

                                # Czas na zastanowienie się
                                think_sec = random.uniform(config.THINKING_DELAY_MIN, config.THINKING_DELAY_MAX) / 1000.0
                                logger.info(f"🤔 Zastanawiam się ({think_sec:.1f}s)...")
                                await asyncio.sleep(think_sec)

                                candidates = self.firebase.get_all_answers(polish_word, current_detected_lang)
                                captured_correct_word = None

                                failed_set = failed_answers_per_word.get(norm_pl, set())
                                answer = None

                                # 1. Wybierz pierwszego kandydata z bazy, który NIE zawiódł jeszcze w tej sesji
                                for cand in candidates:
                                    if cand.strip().lower() not in failed_set:
                                        answer = cand
                                        break

                                # 2. Jeśli wszystkie znane odpowiedzi zawiodły w tej sesji:
                                if not answer and candidates:
                                    # Sprawdź czy kropka była już wpisywana dla tego słowa w tej sesji
                                    if dots_typed_per_word.get(norm_pl, 0) >= 1:
                                        # Kropka była już użyta! Resetujemy failed_set, aby uniknąć pętli kropki
                                        logger.info(f"🔄 Słowo '{polish_word}': resetuję blokadę failed_set (kropka była już użyta). Próbuję: '{candidates[0]}'")
                                        failed_answers_per_word[norm_pl].clear()
                                        answer = candidates[0]
                                    else:
                                        logger.warning(f"⚠️ Wszystkie znane odpowiedzi dla '{polish_word}' ({candidates}) zawiodły! Pobieram poprawkę.")
                                        answer = None

                                typed_answer = answer
                                if answer:
                                    logger.info(f"🎯 Znam z bazy ({current_detected_lang.upper()}): '{answer}'")
                                    await self.human_type(page, "#answer", answer)
                                    result["correct_words"] += 1
                                else:
                                    # Nigdy nie zostawiaj pustego pola (InstaLing ignoruje pusty submit)! Wpisujemy kropkę:
                                    dots_typed_per_word[norm_pl] = dots_typed_per_word.get(norm_pl, 0) + 1
                                    logger.info("❓ Nieznane słowo -> wpisuję '.' aby pobrać poprawną odpowiedź")
                                    await page.click("#answer")
                                    await page.fill("#answer", ".")
                                    await page.evaluate("""() => {
                                        const a = document.getElementById('answer');
                                        if (a) {
                                            a.value = '.';
                                            a.dispatchEvent(new Event('input', { bubbles: true }));
                                            a.dispatchEvent(new Event('change', { bubbles: true }));
                                        }
                                    }""")

                                result["total_words"] += 1

                                # Pauza przed kliknięciem
                                action_sec = random.uniform(config.ACTION_DELAY_MIN, config.ACTION_DELAY_MAX) / 1000.0
                                await asyncio.sleep(action_sec)

                                # Kliknij Sprawdź
                                try:
                                    await chk_loc.first.click()
                                except Exception:
                                    pass
                                await page.evaluate("""() => {
                                    const c = document.getElementById('check') || document.querySelector('#check .btn');
                                    if (c && c.offsetParent !== null) c.click();
                                }""")

                                # Czekaj na odpowiedź sieciową (XHR)
                                for _ in range(20):
                                    await asyncio.sleep(0.1)
                                    if captured_correct_word:
                                        break

                                # Zapisz nowe słowo jeśli przechwyciliśmy
                                if captured_correct_word:
                                    clean_cw = captured_correct_word.strip()
                                    if not self.firebase.looks_polish(clean_cw) and not self.firebase.is_junk(clean_cw):
                                        # Poprawna odpowiedź ZAWSZE jest usuwana z failed_set (serwer potwierdził, że jest dobra!)
                                        if norm_pl in failed_answers_per_word:
                                            failed_answers_per_word[norm_pl].discard(clean_cw.lower())

                                        # Jeśli odpowiedź którą wpisaliśmy była inna niż poprawna -> oznacz tę konkretną jako błędną w tej sesji
                                        if typed_answer and typed_answer.strip().lower() != clean_cw.lower():
                                            if norm_pl not in failed_answers_per_word:
                                                failed_answers_per_word[norm_pl] = set()
                                            failed_answers_per_word[norm_pl].add(typed_answer.strip().lower())
                                            logger.info(f"❌ Odpowiedź '{typed_answer}' była błędna dla tego pytania! Poprawna to: '{clean_cw}'")

                                        if current_detected_lang not in self.firebase.cache:
                                            self.firebase.cache[current_detected_lang] = {}

                                        # Zapisz dla wszystkich synonimów w pytaniu (np. 'nieporządny; niechlujny, zaniedbany')
                                        parts = [p.strip() for p in re.split(r"[,;]", norm_pl) if p.strip()]
                                        for part in parts:
                                            self.firebase.cache[current_detected_lang][part] = clean_cw

                                        main_key = parts[0] if parts else norm_pl
                                        if not typed_answer or typed_answer.strip().lower() != clean_cw.lower():
                                            saved = await self.firebase.save_word(main_key, clean_cw, current_detected_lang)
                                            if saved:
                                                result["learned_words"] += 1

                                # Czas na obejrzenie wyniku
                                if answer:
                                    view_sec = random.uniform(config.NEXT_WORD_DELAY_MIN, config.NEXT_WORD_DELAY_MAX) / 1000.0
                                else:
                                    view_sec = random.uniform(2.0, 3.5)
                                await asyncio.sleep(view_sec)

                                # Kliknij Następne lub Enter — czekaj aż pojawi się przycisk
                                nxt = page.locator("#next_word:visible, #nextword:visible, #comment_back_button:visible")
                                for _wait in range(15):
                                    if await nxt.count() > 0:
                                        break
                                    await asyncio.sleep(0.3)
                                if await nxt.count() > 0:
                                    try:
                                        await nxt.first.click()
                                    except Exception:
                                        pass
                                await page.evaluate("""() => {
                                    const n = document.getElementById('next_word') || document.getElementById('nextword') || document.getElementById('comment_back_button');
                                    if (n && n.offsetParent !== null) n.click();
                                }""")
                                await asyncio.sleep(0.6)
                                last_progress_iter = iterations
                                stall_logged = False
                                continue

                    # ===== STALL DETECTION =====
                    # Jeśli żaden handler nie dopasował (brak 'continue'), wykryj stall
                    if iterations - last_progress_iter > 30:
                        if not stall_logged:
                            stall_logged = True
                            # Diagnostyka: co jest widoczne na stronie?
                            try:
                                diag = await page.evaluate("""() => {
                                    return Array.from(document.querySelectorAll('*'))
                                        .filter(el => {
                                            const s = window.getComputedStyle(el);
                                            return s.display !== 'none' && s.visibility !== 'hidden' && el.offsetHeight > 0;
                                        })
                                        .map(el => ({id: el.id, tag: el.tagName, text: (el.innerText || '').trim().slice(0, 60)}))
                                        .filter(x => x.id && x.text);
                                }""")
                                logger.warning(f"🛑 STALL DETECTED! {iterations - last_progress_iter} iteracji bez postępu. Widoczne elementy: {diag}")
                            except Exception:
                                logger.warning(f"🛑 STALL DETECTED! {iterations - last_progress_iter} iteracji bez postępu (nie udało się pobrać diagnostyki)")

                        # Awaryjne kliknięcie dowolnego przycisku / Enter
                        try:
                            emergency = await page.evaluate("""() => {
                                // Zamknij consent overlay
                                document.querySelectorAll('.fc-consent-root, .fc-dialog-overlay, .fc-dialog-container').forEach(el => el.remove());
                                if (document.body) { document.body.style.overflow = 'auto'; document.body.style.pointerEvents = 'auto'; }
                                // Kliknij dowolny widoczny przycisk z sensownym tekstem
                                const btns = document.querySelectorAll('#next_word, #nextword, #check, #continue_session_button, #start_session_button, #comment_back_button, #know_new, #know_word, #skip_word, #skip');
                                for (const b of btns) {
                                    if (b && b.offsetParent !== null) { b.click(); return 'clicked:' + b.id; }
                                }
                                return 'nothing';
                            }""")
                            if emergency != 'nothing':
                                logger.info(f"🔧 Awaryjne kliknięcie: {emergency}")
                                last_progress_iter = iterations
                                stall_logged = False
                        except Exception:
                            pass
                        await page.keyboard.press("Enter")

                    if iterations - last_progress_iter > 60:
                        logger.error("🛑 Bot utknął na >60 iteracji bez postępu. Przerywam sesję.")
                        result["message"] = "❌ Bot utknął — sesja przerwana (brak postępu)"
                        break

                    await asyncio.sleep(0.5)

            except Exception as e:
                logger.error(f"Wyjątek podczas rozwiązywania sesji: {e}", exc_info=True)
                result["message"] = f"❌ Wystąpił błąd: {str(e)}"
            finally:
                result["time_seconds"] = round(time.time() - start_time, 1)
                await browser.close()

        return result
