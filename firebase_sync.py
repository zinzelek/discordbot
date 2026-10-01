import asyncio
import logging
import re
import time
import aiohttp
from typing import Optional, Dict, Any

import config

logger = logging.getLogger("InstaLingBot.Firebase")

class FirebaseClient:
    def __init__(self):
        self.api_key = "AIzaSyApYxO_yrl-WZRlQdYSeInyhmw-ZFyvsT8"
        self.db_url = config.FIREBASE_DATABASE_URL.rstrip('/')
        self.id_token = None
        self.token_expiry = 0
        self.cache: Dict[str, Dict[str, str]] = {
            "de": {},
            "en": {}
        }
        self.banned_phrases = [
            'instaling', 'logowanie', 'słownictwo', 'strona główna', 'zaloguj',
            'hasło', 'uczeń', 'nauczyciel', 'sesja', 'wykonaj', 'menu',
            'wyloguj', 'regulamin', 'polityka prywatności', 'kontakt',
            'zalogowany', 'twoje konto', 'instaling.pl', 'panel', 'pomoc'
        ]
        self.forbidden_words = {
            'brawo', 'dobrze', 'źle', 'zle', 'wynik', 'punkt', 'punkty', 'sekund', 'czas',
            'znam', 'nie znam', 'nieznam', 'wiem', 'nie wiem', 'niewiem',
            'zgadzam się', 'zgadzam sie', 'rozumiem', 'spróbuj ponownie', 'sprobuj ponownie',
            'powtórz', 'powtorz', 'prüfen', 'sprawdź', 'sprawdz'
        }

    async def get_token(self, force_refresh: bool = False) -> str | None:
        """Pobiera lub odświeża token anonimowego logowania do Firebase z uwzględnieniem czasu wygaśnięcia"""
        now = time.time()
        if not force_refresh and self.id_token and now < (self.token_expiry - 300):
            return self.id_token

        auth_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={self.api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(auth_url, json={"returnSecureToken": True}, timeout=aiohttp.ClientTimeout(total=8.0)) as res:
                    if res.status == 200:
                        data = await res.json()
                        self.id_token = data.get("idToken")
                        expires_in = int(data.get("expiresIn", 3600))
                        self.token_expiry = now + expires_in
                        logger.info("🔑 Pomyślnie odświeżono token Firebase.")
                        return self.id_token
                    else:
                        logger.error(f"Błąd autoryzacji Firebase: status {res.status}")
        except Exception as e:
            logger.error(f"Wyjątek autoryzacji Firebase: {e}")
        return None

    def looks_polish(self, text: str) -> bool:
        return bool(re.search(r"[ąćęłńóśźżĄĆĘŁŃÓŚŹŻ]", text))

    def is_junk(self, text: str) -> bool:
        if not text:
            return True
        t = text.lower().strip()
        if len(t) > 120:
            return True
        for p in self.banned_phrases:
            if p in t:
                return True
        if t in self.forbidden_words:
            return True
        words = [w for w in t.split() if w]
        if len(words) > 12:
            return True
        if len(words) > 2:
            pl_stop = {
                'jest', 'jestem', 'ma', 'mieć', 'być', 'oraz', 'ale', 'lub', 'dla',
                'od', 'do', 'przez', 'nad', 'pod', 'przed', 'za', 'bez', 'jego',
                'jej', 'ich', 'nasz', 'wasz', 'mój', 'twój', 'swój', 'ten', 'ta',
                'to', 'ci', 'się', 'nie', 'tak', 'także', 'forma', 'sesji'
            }
            cnt = sum(1 for w in words if w in pl_stop)
            if cnt / len(words) > 0.6:
                return True
        return False

    def sanitize_key(self, key: str) -> str:
        # Usuń znaki niedozwolone w ścieżkach Firebase Realtime Database
        return re.sub(r"[.#$\[\]/]", "", key).strip().lower()

    async def fetch_database(self) -> dict:
        """Pobiera wszystkie słówka z bazy Firebase dla DE i EN z auto-retry na 401"""
        token = await self.get_token()
        url = f"{self.db_url}/words.json"
        if token:
            url += f"?auth={token}"

        for attempt in range(2):
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(url, timeout=aiohttp.ClientTimeout(total=10.0)) as res:
                        if res.status == 200:
                            data = await res.json() or {}
                            de_dict = data.get("de", {}) or {}
                            en_dict = data.get("en", {}) or {}
                            
                            # Wyczyść i zapisz w pamięci cache
                            self.cache["de"] = {k.lower().strip(): v.strip() for k, v in de_dict.items() if not self.is_junk(v)}
                            self.cache["en"] = {k.lower().strip(): v.strip() for k, v in en_dict.items() if not self.is_junk(v)}
                            
                            logger.info(f"☁️ Zsynchronizowano bazę Firebase: DE: {len(self.cache['de'])} | EN: {len(self.cache['en'])}")
                            return self.cache
                        elif res.status == 401 and attempt == 0:
                            logger.warning("Firebase 401: Wymuszam natychmiastowe odświeżenie tokena...")
                            token = await self.get_token(force_refresh=True)
                            url = f"{self.db_url}/words.json"
                            if token:
                                url += f"?auth={token}"
                            continue
                        else:
                            logger.warning(f"Błąd pobierania bazy Firebase: status {res.status}")
            except Exception as e:
                logger.error(f"Wyjątek podczas łączenia z Firebase: {e}")
        return self.cache

    def get_answer(self, polish_text: str, lang: str = "en") -> str | None:
        """Zwraca odpowiedź z lokalnego cache dla podanego języka"""
        lang = lang.lower().strip()
        dict_data = self.cache.get(lang, {})
        if not dict_data:
            return None

        clean_pl = polish_text.strip().lower()

        # Dokładne dopasowanie
        full_key = clean_pl
        safe_full_key = self.sanitize_key(full_key)
        for k in (full_key, safe_full_key):
            if k in dict_data:
                val = dict_data[k].split(";")[0].split(",")[0].strip()
                if val.lower() != full_key and not self.looks_polish(val) and not self.is_junk(val):
                    return val

        # Sprawdź części przed średnikiem lub przecinkiem
        parts = [p.strip().lower() for p in re.split(r"[,;]", polish_text) if p.strip()]
        for p in parts:
            safe_p = self.sanitize_key(p)
            for k in (p, safe_p):
                if k in dict_data:
                    val = dict_data[k].split(";")[0].split(",")[0].strip()
                    if val.lower() != p and not self.looks_polish(val) and not self.is_junk(val):
                        return val

        return None

    async def save_word(self, polish_word: str, translation: str, lang: str = "en") -> bool:
        """Zapisuje nowe słówko do bazy Firebase oraz lokalnego cache z auto-retry na 401"""
        lang = lang.lower().strip()
        k = polish_word.strip().lower()
        v = translation.strip()

        if not k or not v or self.looks_polish(v) or self.is_junk(v) or v.lower() == k:
            logger.warning(f"🛑 [SAVE REJECTED] Odrzucono: '{k}' = '{v}'")
            return False

        # Zaktualizuj pamięć lokalną ZAWSZE
        if lang not in self.cache:
            self.cache[lang] = {}
        self.cache[lang][k] = v

        safe_k = self.sanitize_key(k)
        if safe_k != k:
            self.cache[lang][safe_k] = v

        target_key = safe_k or k
        token = await self.get_token()

        for attempt in range(2):
            url = f"{self.db_url}/words/{lang}/{target_key}.json"
            if token:
                url += f"?auth={token}"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.put(url, json=v, timeout=aiohttp.ClientTimeout(total=8.0)) as res:
                        if res.status in (200, 204):
                            logger.info(f"💾 Baza Firebase UPDATE: {lang}/{target_key} = {v}")
                            return True
                        elif res.status == 401 and attempt == 0:
                            logger.warning("Firebase PUT 401: Wymuszam odświeżenie tokena i ponawiam zapis...")
                            token = await self.get_token(force_refresh=True)
                            continue
                        else:
                            logger.error(f"Błąd zapisu do Firebase: status {res.status}")
            except Exception as e:
                logger.error(f"Wyjątek podczas zapisu do Firebase: {e}")
        return False

    async def create_license(self, license_type: str = "WEEK", creator: str = "discord-bot") -> Optional[str]:
        """Generuje nowy klucz licencyjny do skryptu i zapisuje go w Firebase"""
        import random
        import string

        license_type = license_type.upper().strip()
        if license_type not in ("WEEK", "MONTH", "LIFETIME", "DAY"):
            license_type = "WEEK"

        chars = string.ascii_uppercase + string.digits
        token = await self.get_token()

        for _ in range(10):
            part1 = ''.join(random.choices(chars, k=4))
            part2 = ''.join(random.choices(chars, k=4))
            key = f"VIP-{part1}-{part2}"

            now_ms = int(time.time() * 1000)
            td = {"DAY": 86400000, "WEEK": 604800000, "MONTH": 2592000000, "LIFETIME": -1}
            duration = td.get(license_type, 604800000)
            expires_at = -1 if duration == -1 else (now_ms + duration)

            data = {
                "createdAt": now_ms,
                "createdBy": creator,
                "expiresAt": expires_at,
                "status": "unused",
                "type": license_type
            }

            url = f"{self.db_url}/licenses/{key}.json"
            if token:
                url += f"?auth={token}"

            try:
                async with aiohttp.ClientSession() as session:
                    async with session.put(url, json=data, timeout=aiohttp.ClientTimeout(total=8.0)) as res:
                        if res.status in (200, 204):
                            logger.info(f"🔑 Wygenerowano klucz licencyjny do skryptu: {key} ({license_type})")
                            return key
            except Exception as e:
                logger.error(f"Wyjątek podczas tworzenia licencji: {e}")
        return None

    async def get_all_licenses(self) -> Dict[str, Any]:
        """Pobiera wszystkie klucze licencyjne skryptu z Firebase"""
        token = await self.get_token()
        url = f"{self.db_url}/licenses.json"
        if token:
            url += f"?auth={token}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=8.0)) as res:
                    if res.status == 200:
                        return await res.json() or {}
        except Exception as e:
            logger.error(f"Błąd pobierania licencji z Firebase: {e}")
        return {}

    async def delete_license(self, key: str) -> bool:
        """Usuwa klucz licencyjny skryptu z Firebase"""
        key = key.strip().upper()
        token = await self.get_token()
        url = f"{self.db_url}/licenses/{key}.json"
        if token:
            url += f"?auth={token}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.delete(url, timeout=aiohttp.ClientTimeout(total=8.0)) as res:
                    return res.status in (200, 204)
        except Exception as e:
            logger.error(f"Błąd usuwania licencji {key}: {e}")
        return False
