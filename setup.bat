@echo off
setlocal enabledelayedexpansion
REM ShinAgent Setup Bootstrap for plain Windows cmd.exe (no Git Bash needed).
REM Run this once after cloning: setup.bat

echo.
echo   ==================================
echo    ShinAgent Setup
echo    ShinTech Electronics
echo   ==================================
echo.

REM --- Check Python ---
python --version >nul 2>&1
if errorlevel 1 (
  echo ERROR: Python not found. Install from python.org, then re-run this script.
  pause
  exit /b 1
)

REM --- Idempotency: already set up? ---
if exist ".env" if exist "config\config.yaml" (
  echo It looks like ShinAgent has already been set up ^(.env exists^).
  echo.
  echo   1^) Re-run the setup wizard ^(review/change existing config^)
  echo   2^) Skip setup and start ShinAgent now
  echo   3^) Exit
  echo.
  set /p CHOICE="Choose [1/2/3]: "
  if "!CHOICE!"=="2" (
    python main.py --text
    exit /b 0
  )
  if "!CHOICE!"=="3" (
    echo Exiting.
    exit /b 0
  )
  echo Re-running setup wizard...
)

REM --- Create virtual environment if not exists ---
if not exist ".venv" (
  echo Creating virtual environment...
  python -m venv .venv
)

call .venv\Scripts\activate.bat

REM --- Install minimal deps for the wizard itself ---
echo Installing setup dependencies...
pip install --quiet flask requests pyyaml

REM --- Launch the setup wizard ---
echo.
echo Starting setup wizard...
echo Open your browser to: http://localhost:8080/setup
echo.

start "" "http://localhost:8080/setup"
python setup_wizard.py

echo.
echo   ==================================
echo    Setup wizard closed.
echo    Web app:  http://localhost:8766
echo    Settings: http://localhost:8766/settings
echo    Start:    python main.py --text
echo   ==================================
echo.
pause
