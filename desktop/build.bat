@echo off
REM ============================================================================
REM  PharmaDost — build the Windows desktop app (.exe)
REM  Run this from the PROJECT ROOT (the folder with manage.py), by double-click
REM  or:  desktop\build.bat
REM ============================================================================
setlocal
cd /d "%~dp0.."

echo.
echo === PharmaDost desktop build =============================================
echo.

REM --- use the project virtualenv if present -------------------------------
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
)

echo [1/3] Installing/refreshing build dependencies ...
python -m pip install --upgrade pip >nul
python -m pip install -r requirements.txt
python -m pip install waitress whitenoise pyinstaller pywebview
if errorlevel 1 goto :error

echo.
echo [2/3] Collecting static files ...
python manage.py collectstatic --noinput
if errorlevel 1 goto :error

echo.
echo [3/3] Building the .exe with PyInstaller ...
pyinstaller desktop\PharmaDost.spec --noconfirm
if errorlevel 1 goto :error

echo.
echo === DONE =================================================================
echo   Your app is in:  dist\PharmaDost\PharmaDost.exe
echo   Zip the whole  dist\PharmaDost  folder and share it.
echo =========================================================================
echo.
pause
exit /b 0

:error
echo.
echo *** BUILD FAILED — see the messages above. ***
echo.
pause
exit /b 1
