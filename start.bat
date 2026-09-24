@echo off
setlocal enabledelayedexpansion
title InstaLing Discord Bot - dc. zinzelekk

echo ======================================================
echo    InstaLing Discord Bot (DE + EN) - Offline Runner
echo ======================================================
echo.

set "PY_CMD="

python --version >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=python"
    goto :found_python
)

py --version >nul 2>&1
if !errorlevel! equ 0 (
    set "PY_CMD=py"
    goto :found_python
)

if exist "%LOCALAPPDATA%\Programs\Python\Python314\python.exe" (
    set "PY_CMD=%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
    goto :found_python
)

for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python*") do (
    if exist "%%D\python.exe" (
        set "PY_CMD=%%D\python.exe"
        goto :found_python
    )
)

:found_python
if "%PY_CMD%"=="" (
    echo [BLAD] Nie znaleziono Pythona w systemie!
    echo Pobierz Pythona z: https://www.python.org/downloads/
    pause
    exit /b 1
)

echo [OK] Uzywam Pythona: %PY_CMD%
echo.

echo [1/2] Sprawdzanie i instalacja wymaganych bibliotek...
%PY_CMD% -m pip install --upgrade pip >nul 2>&1
%PY_CMD% -m pip install -r requirements.txt
%PY_CMD% -m playwright install chromium

echo.
echo [2/2] Uruchamianie bota Discord...
echo.
%PY_CMD% bot.py

pause
