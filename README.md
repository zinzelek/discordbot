# 🤖 InstaLing Discord Bot (DE + EN) — Niewykrywalny Automatyczny Rozwiązywacz Sesji

Zaawansowany bot Discord w Pythonie, który automatycznie i w tle rozwiązuje codzienne sesje na portalu **InstaLing.pl** zarówno dla języka **niemieckiego (DE)**, jak i **angielskiego (EN)**.

Bot korzysta z tej samej centralnej bazy słówek w chmurze **Firebase Realtime Database**, co userscript — dzięki czemu natychmiast zna tysiące słówek i uczy się nowych w czasie rzeczywistym!

---

## ✨ Kluczowe Funkcje

- 🛡️ **Pełna niewykrywalność (Playwright Stealth)**:
  - Usunięta flaga `navigator.webdriver`.
  - Naturalna emulacja wpisywania tekstu znak po znaku (losowe opóźnienia 30-80ms na znak).
  - Prawdziwy User-Agent nowoczesnej przeglądarki Chrome na Windowsie.
- ⚡ **Błyskawiczne przechwytywanie odpowiedzi (XHR Interceptor)**:
  - Przechwytuje poprawne odpowiedzi bezpośrednio z ruchu sieciowego serwera InstaLing.
  - Automatycznie zapisuje nowe słówka do Firebase (wspiera idiomy i frazy wielowyrazowe).
- 🧠 **Pełna automatyzacja nawigacji**:
  - Samodzielnie klika *"Zacznij dzisiejszą sesję"*.
  - Automatycznie klika *"ZNAM"* oraz natychmiastowe *"POMIŃ"*.
  - Zamyka wyskakujące okienka i modale.
- ⏰ **Automatyczny harmonogram (Auto-Daily)**:
  - Może codziennie rano (domyślnie o 7:00) sam wykonać sesję dla Twoich kont i wysłać Ci raport na Discordzie!
- 🔒 **Bezpieczeństwo**:
  - Komendy wprowadzania danych są prywatne (`ephemeral`) — nikt inny na serwerze nie zobaczy Twoich haseł ani loginów.
  - Dane przechowywane lokalnie w bezpiecznej bazie SQLite (`accounts.db`).

---

## 🚀 Szybki Start (Instrukcja Krok po Kroku)

### 1. Pobranie i instalacja Pythona
Upewnij się, że masz zainstalowanego Pythona (wersja 3.10 lub nowsza).
> ⚠️ **Ważne**: Podczas instalacji Pythona koniecznie zaznacz opcję: **`Add python.exe to PATH`**.

### 2. Utworzenie Bota na Discordzie (2 minuty)
1. Wejdź na [Discord Developer Portal](https://discord.com/developers/applications).
2. Kliknij **New Application**, nadaj nazwę (np. *InstaLing Bot*) i zatwierdź.
3. W lewym menu przejdź do zakładki **Bot**:
   - Kliknij **Reset Token** i skopiuj swój **Token Bota** (będzie potrzebny za chwilę).
   - Zjedź niżej do sekcji **Privileged Gateway Intents** i włącz:
     - ✅ **Server Members Intent**
     - ✅ **Message Content Intent**
   - Zapisz zmiany (*Save Changes*).
4. Przejdź do zakładki **OAuth2** -> **URL Generator**:
   - W sekcji *Scopes* zaznacz: `bot` oraz `applications.commands`.
   - W sekcji *Bot Permissions* zaznacz: `Administrator` (lub `Send Messages`, `Embed Links`, `Attach Files`).
   - Skopiuj wygenerowany link na dole i wklej go w przeglądarce, aby dodać bota na swój serwer Discord.

### 3. Konfiguracja bota
1. Otwórz plik `.env` w folderze bota w Notatniku lub edytorze kodu.
2. Wklej swój token bota:
```env
DISCORD_TOKEN=twoj_skopiowany_token_tutaj
```
3. (Opcjonalnie) Możesz zmienić:
   - `REQUIRED_ROLE_NAME=VIP` — **tylko osoby z tą rangą mogą używać bota!** (możesz wpisać np. `VIP`, `Klient`, `InstaLing` itp., albo zostawić puste `""` jeśli każdy ma mieć dostęp). Administratorzy serwera mają dostęp zawsze.
   - `REQUIRED_ROLE_ID=0` — jeśli wolisz podać dokładne ID rangi zamiast nazwy.
   - `HEADLESS=False` — jeśli chcesz widzieć na żywo wyskakujące okno Chrome, jak bot sam pisze i klika słówka!
   - `DEFAULT_AUTO_HOUR=7` — godzina codziennego automatycznego rozwiązywania sesji.

### 4. Uruchomienie bota
Wystarczy dwukrotnie kliknąć plik:
👉 **`start.bat`**

Skrypt sam zainstaluje wymagane biblioteki (`discord.py`, `playwright`, `httpx`, `apscheduler`), pobierze silnik przeglądarki Chromium i uruchomi bota!

---

## 🎮 Komendy Slash w Discordzie

Po uruchomieniu bot zarejestruje komendy na Discordzie:

| Komenda | Opis |
| :--- | :--- |
| `/dodaj_konto` | Dodaje konto InstaLing do bota (login, hasło, język: `auto`/`de`/`en`). |
| `/sesja` | Rozwiązuje sesję od ręki dla wskazanego konta LUB wszystkich Twoich kont na raz. |
| `/konta` | Wyświetla listę Twoich zapisanych kont, przypisany język i czas ostatniej sesji. |
| `/usun_konto` | Usuwa zapisane konto z bazy bota. |
| `/baza` | Pokazuje aktualny stan bazy Firebase (liczbę zapamiętanych słówek DE i EN). |

---

## 📁 Struktura Projektu

- `bot.py` — Główny proces bota Discord i obsługa komend Slash.
- `solver.py` — Zaawansowany silnik automatyzacji Playwright (anti-detect, XHR interceptor, pętla rozwiązywania).
- `firebase_sync.py` — Klient synchronizacji z chmurą Firebase Realtime Database.
- `database.py` — Lokalna baza danych SQLite (`accounts.db`) przechowująca konta użytkowników.
- `scheduler.py` — Harmonogram automatycznego uruchamiania sesji o określonej godzinie.
- `config.py` — Konfiguracja parametrów i zmiennych środowiskowych.
- `start.bat` — 1-klikowy plik uruchomieniowy dla systemu Windows.
