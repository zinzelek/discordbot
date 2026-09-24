# Plik konfiguracyjny Bota Discord InstaLing

import os
from dotenv import load_dotenv

load_dotenv()

# Token Twojego bota Discord (z Discord Developer Portal: https://discord.com/developers/applications)
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN", "WSTAW_TUTAJ_TOKEN_BOTA_DISCORD")

# URL bazy Firebase Realtime Database (dokładnie ta sama, z której korzysta userscript)
FIREBASE_DATABASE_URL = os.getenv(
    "FIREBASE_DATABASE_URL",
    "https://instaling-bot-2362a-default-rtdb.europe-west1.firebasedatabase.app"
)

# Czy przeglądarka ma działać w tle (niewidoczna)?
# True = działa cicho w tle (rekomendowane na serwer/VPS)
# False = widać wyskakujące okno przeglądarki Chrome na żywo (super do podglądu)
HEADLESS = os.getenv("HEADLESS", "True").lower() in ("true", "1", "yes")

# Emulacja człowieka (w milisekundach - spokojne, ludzkie tempo w 100% niewykrywalne)
TYPING_SPEED_MIN = int(os.getenv("TYPING_SPEED_MIN", "70"))     # 70ms - 150ms na znak
TYPING_SPEED_MAX = int(os.getenv("TYPING_SPEED_MAX", "150"))
THINKING_DELAY_MIN = int(os.getenv("THINKING_DELAY_MIN", "2000")) # 2.0s - 3.8s na "zastanowienie się" i przeczytanie pytania
THINKING_DELAY_MAX = int(os.getenv("THINKING_DELAY_MAX", "3800"))
ACTION_DELAY_MIN = int(os.getenv("ACTION_DELAY_MIN", "800"))    # 0.8s - 1.6s przed kliknięciem Sprawdź
ACTION_DELAY_MAX = int(os.getenv("ACTION_DELAY_MAX", "1600"))
NEXT_WORD_DELAY_MIN = int(os.getenv("NEXT_WORD_DELAY_MIN", "1400")) # 1.4s - 2.5s na podgląd odpowiedzi
NEXT_WORD_DELAY_MAX = int(os.getenv("NEXT_WORD_DELAY_MAX", "2500"))

# Losowe paczki kont naraz (np. raz 5, potem 3, potem 2, potem 4)
BATCH_SIZE_MIN = int(os.getenv("BATCH_SIZE_MIN", "2"))          # min. kont w paczce
BATCH_SIZE_MAX = int(os.getenv("BATCH_SIZE_MAX", "5"))          # max. kont w paczce
BATCH_PAUSE_MIN = int(os.getenv("BATCH_PAUSE_MIN", "6"))        # min. sekund przerwy między paczkami
BATCH_PAUSE_MAX = int(os.getenv("BATCH_PAUSE_MAX", "15"))       # max. sekund przerwy między paczkami

# Domyślna godzina automatycznego wykonywania sesji (np. 7 rano)
DEFAULT_AUTO_HOUR = int(os.getenv("DEFAULT_AUTO_HOUR", "7"))
DEFAULT_AUTO_MINUTE = int(os.getenv("DEFAULT_AUTO_MINUTE", "0"))

# Ograniczenie uprawnień do 1 rangi
# Możesz wpisać nazwę roli (np. "VIP", "Klient", "InstaLing") LUB jej dokładne ID z Discorda
# Domyślnie: "VIP". Jeśli ustawisz pusty "" i ID 0, bot będzie dostępny dla każdego.
REQUIRED_ROLE_NAME = os.getenv("REQUIRED_ROLE_NAME", "VIP")
REQUIRED_ROLE_ID = int(os.getenv("REQUIRED_ROLE_ID", "0"))
