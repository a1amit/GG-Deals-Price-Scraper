@echo off
echo ========================================
echo   Building GG Deals Scraper .exe
echo ========================================
echo.

REM Activate venv if it exists
if exist "venv\Scripts\activate.bat" (
    call venv\Scripts\activate.bat
)

REM Install PyInstaller if not present
pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    pip install pyinstaller
)

echo.
echo Running PyInstaller...
pyinstaller --clean "GG_Deals_Scraper.spec"

if errorlevel 1 (
    echo.
    echo BUILD FAILED! Check the errors above.
    pause
    exit /b 1
)

echo.
echo ========================================
echo   Build complete!
echo   Output: dist\GG Deals Scraper.exe
echo ========================================
echo.
pause
