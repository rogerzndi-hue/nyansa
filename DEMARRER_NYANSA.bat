@echo off
setlocal
cd /d "%~dp0"

if not exist "%~dp0.venv\Scripts\python.exe" (
  echo Environnement virtuel NYANSA introuvable.
  echo Executez d'abord la procedure d'installation.
  pause
  exit /b 1
)

cd /d "%~dp0backend"
start "NYANSA" cmd /k ""%~dp0.venv\Scripts\python.exe" -m nyansa.devserver"
timeout /t 2 /nobreak >nul
start "" "http://127.0.0.1:8000"

endlocal
