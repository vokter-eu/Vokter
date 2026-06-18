@echo off
setlocal enabledelayedexpansion
title Vokter Installer

cls
echo.
echo  ===============================================================
echo    ^|^|  Vokter -- Personal AI Agent -- Installer for Windows
echo  ===============================================================
echo.

:: ── Step 1: Check Docker ──────────────────────────────────────────────
docker --version >nul 2>&1
if %errorlevel% neq 0 (
    echo  Docker Desktop is not installed.
    echo.
    echo  Docker is a free app that lets Vokter run safely
    echo  on your PC. Opening the download page now...
    echo.
    start "" "https://www.docker.com/products/docker-desktop/"
    echo  Install Docker Desktop, then run this file again.
    echo.
    pause
    exit /b 0
)

:: ── Step 2: Start Docker if not running ──────────────────────────────
docker info >nul 2>&1
if %errorlevel% neq 0 (
    echo  Docker is installed but not running. Starting it...
    start "" "C:\Program Files\Docker\Docker\Docker Desktop.exe"
    echo  Waiting for Docker to start (this may take 30-60 seconds)...
    :wait_docker
    timeout /t 3 /nobreak >nul
    docker info >nul 2>&1
    if %errorlevel% neq 0 goto wait_docker
)

echo  [OK] Docker is running
echo.

:: ── Step 3: Create Vokter folder ────────────────────────────────────
set "VOKTER_DIR=%USERPROFILE%\Vokter"
if not exist "%VOKTER_DIR%" mkdir "%VOKTER_DIR%"
cd /d "%VOKTER_DIR%"
echo  Vokter folder: %VOKTER_DIR%

:: ── Step 4: Download configuration ──────────────────────────────────
echo  Downloading Vokter...
curl -fsSL "https://raw.githubusercontent.com/vokter-eu/Vokter/main/docker-compose.yml" -o docker-compose.yml
if %errorlevel% neq 0 (
    echo.
    echo  ERROR: Could not download Vokter.
    echo  Please check your internet connection and try again.
    pause
    exit /b 1
)

:: ── Step 5: Generate encrypted config ───────────────────────────────
if not exist .env (
    curl -fsSL "https://raw.githubusercontent.com/vokter-eu/Vokter/main/.env.example" -o .env
    for /f "delims=" %%k in ('powershell -NoProfile -Command ^
        "[System.BitConverter]::ToString([System.Security.Cryptography.RandomNumberGenerator]::GetBytes(32)).Replace('-','').ToLower()"') ^
        do set "DB_KEY=%%k"
    powershell -NoProfile -Command ^
        "(Get-Content .env) -replace '^VOKTER_DB_KEY=.*', 'VOKTER_DB_KEY=!DB_KEY!' | Set-Content .env -Encoding UTF8"
    echo  [OK] Encryption key generated -- your data is protected
) else (
    echo  [OK] Existing configuration found -- keeping it
)

:: ── Step 6: Start Vokter ────────────────────────────────────────────
echo.
echo  Starting Vokter...
docker compose up -d

:: ── Step 7: Download AI models ──────────────────────────────────────
echo.
echo  Downloading AI model -- llama3.2:3b (~2 GB)
echo  This takes 5-10 minutes the first time.
echo  Please wait, do not close this window.
echo.
docker exec vokter-ollama ollama pull llama3.2:3b
docker exec vokter-ollama ollama pull nomic-embed-text

:: ── Step 8: Open browser ────────────────────────────────────────────
echo.
echo  ===============================================================
echo    Vokter is ready!
echo.
echo    Opening http://localhost:8080 in your browser...
echo.
echo    Next time: Vokter starts automatically with Docker Desktop.
echo    No need to run this installer again.
echo  ===============================================================
echo.
timeout /t 3 /nobreak >nul
start "" "http://localhost:8080"
pause
