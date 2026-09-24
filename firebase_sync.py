import re
import logging
import aiohttp
import config

logger = logging.getLogger("InstaLingBot.Firebase")

class FirebaseClient:
    def __init__(self, db_url: str = config.FIREBASE_DATABASE_URL):
        self.db_url = db_url.rstrip("/")
        self.api_key = "AIzaSyApYxO_yrl-WZRlQdYSeInyhmw-ZFyvsT8"
        self.id_token = None
        self.cache = {
            "de": {},
            "en": {}
        }
        self.banned_phrases = [
            'automatyzacji sesji', 'zabroniona', 'wykryto automatyzację', 'zbyt wiele żądań',
            'spróbuj później', 'odśwież stronę', 'błąd serwera', 'unauthorized', 'forbidden',
            'captcha', 'robot', 'bot detected', 'poprawna odpowiedź', 'twoja odpowiedź',
            'richtige antwort', 'correct answer', 'sesja zakończona', 'koniec sesji',
            'niepoprawnie', 'poprawnie', 'prawidłowa odpowiedź'
        ]
        self.forbidden_words = {
            'niepoprawnie', 'poprawnie', 'błąd', 'blad', 'prawidłowa', 'prawidlowa',
            'odpowiedź', 'odpowiedz', 'twoja', 'spróbuj', 'sprobuj', 'ponownie',
            'zrozumiałem', 'zrozumialem', 'zgadzam', 'zgadza', 'dalej', 'następne',
            'nastepne', 'następny', 'nastepny', 'kontynuuj', 'zamknij', 'poprawna',
            'richtige', 'falsche', 'fałszywa', 'falszywa', 'prawda', 'fałsz',
            'brawo', 'dobrze', 'źle', 'zle', 'wynik', 'punkt', 'punkty', 'sekund', 'czas',
            'znam', 'nie znam', 'nieznam', 'wiem', 'nie wiem', 'niewiem',
            'zgadzam się', 'zgadzam sie', 'rozumiem', 'spróbuj ponownie', 'sprobuj ponownie',
            'powtórz', 'powtorz', 'prüfen', 'sprawdź', 'sprawdz'
        }

    async def get_token(self) -> str | None:
        """Pobiera lub odświeża token anonimowego logowania do Firebase"""
        if self.id_token:
            return self.id_token
        auth_url = f"https://identitytoolkit.googleapis.com/v1/accounts:signUp?key={self.api_key}"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(auth_url, json={"returnSecureToken": True}, timeout=aiohttp.ClientTimeout(total=8.0)) as res:
                    if res.status == 200:
                        data = await res.json()
                        self.id_token = data.get("idToken")
                        return self.id_token
                    else:
                        logger.error(f"Błąd autoryzacji Firebase: {res.status}")
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
        """Pobiera wszystkie słówka z bazy Firebase dla DE i EN"""
        token = await self.get_token()
        url = f"{self.db_url}/words.json"
        if token:
            url += f"?auth={token}"
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
                    else:
                        logger.warning(f"Błąd pobierania bazy Firebase: status {res.status}")
        except Exception as e:
            logger.error(f"Wyjątek podczas łączenia z Firebase: {e}")
        return self.cache

    def get_answer(self, polish_text: str, lang: str = "en") -> str | None:
        """Zwraca odpowiedź z lokalnego cache dla podanego języka"""
        lang = lang.lower().strip()
        dict_data = self.cache.get(lang, {})
        full_key = polish_text.strip().lower()
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
        """Zapisuje nowe słówko do bazy Firebase oraz lokalnego cache"""
        lang = lang.lower().strip()
        k = polish_word.strip().lower()
        v = translation.strip()

        if not k or not v or self.looks_polish(v) or self.is_junk(v) or v.lower() == k:
            logger.warning(f"🛑 [SAVE REJECTED] Odrzucono: '{k}' = '{v}'")
            return False

        # Zaktualizuj pamięć lokalną
        if lang not in self.cache:
            self.cache[lang] = {}
        self.cache[lang][k] = v

        safe_k = self.sanitize_key(k)
        if safe_k != k:
            self.cache[lang][safe_k] = v

        target_key = safe_k or k
        token = await self.get_token()
        url = f"{self.db_url}/words/{lang}/{target_key}.json"
        if token:
            url += f"?auth={token}"

        try:
            async with aiohttp.ClientSession() as session:
                async with session.put(url, json=v, timeout=aiohttp.ClientTimeout(total=8.0)) as res:
                    if res.status in (200, 204):
                        logger.info(f"💾 Baza Firebase UPDATE: {lang}/{target_key} = {v}")
                        return True
                    else:
                        logger.error(f"Błąd zapisu do Firebase: status {res.status}")
        except Exception as e:
            logger.error(f"Wyjątek podczas zapisu do Firebase: {e}")
        return False
