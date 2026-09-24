# Oficjalny obraz Playwright z zainstalowanym Pythonem i wszystkimi bibliotekami do Chromium
FROM mcr.microsoft.com/playwright/python:v1.40.0-jammy

# Ustawienie katalogu roboczego
WORKDIR /app

# Kopiowanie zależności i instalacja pakietów
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pobranie przeglądarki Chromium wewnątrz kontenera
RUN playwright install chromium

# Kopiowanie reszty kodu aplikacji
COPY . .

# Wymuszenie trybu headless w chmurze
ENV HEADLESS=True
ENV PYTHONUNBUFFERED=1

# Uruchomienie bota
CMD ["python", "bot.py"]
