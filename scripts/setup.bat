@echo off
setlocal
rem ================================================================
rem  WALKMAN BRIDGE - one-time setup
rem  Double-click me. Safe to run again at any time.
rem ================================================================
for %%I in ("%~dp0..") do set "ROOT=%%~fI"

echo.
echo  ================================================
echo   WALKMAN BRIDGE - one-time setup
echo  ================================================
echo   Project folder: %ROOT%
echo.

rem --- prerequisites --------------------------------------------------
where python >nul 2>nul
if errorlevel 1 (
    echo  [!] Python was not found on this computer.
    echo      Install Python 3.11 or newer from https://www.python.org/downloads/
    echo      IMPORTANT: tick "Add python.exe to PATH" in the installer.
    echo      Then run this file again.
    echo.
    pause
    exit /b 1
)
where npm >nul 2>nul
if errorlevel 1 (
    echo  [!] Node.js / npm was not found on this computer.
    echo      Install Node.js LTS from https://nodejs.org/ then run this file again.
    echo.
    pause
    exit /b 1
)

rem --- Python environment ---------------------------------------------
if exist "%ROOT%\backend\.venv\Scripts\python.exe" (
    echo  [OK] Python environment already exists - skipping creation.
) else (
    echo  [..] Creating Python environment ^(backend\.venv^) ...
    python -m venv "%ROOT%\backend\.venv"
    if errorlevel 1 (
        echo  [!] Could not create the Python environment.
        echo.
        pause
        exit /b 1
    )
)

echo  [..] Installing Python packages ...
"%ROOT%\backend\.venv\Scripts\python.exe" -m pip install --disable-pip-version-check -q -r "%ROOT%\backend\requirements.txt"
if errorlevel 1 (
    echo  [!] Package install failed. Check your internet connection and run this again.
    echo.
    pause
    exit /b 1
)
echo  [OK] Python packages installed.

rem --- Frontend (dashboard) -------------------------------------------
if exist "%ROOT%\frontend\dist\index.html" (
    echo  [OK] Dashboard already built - skipping.
) else (
    echo  [..] Downloading dashboard packages ^(this can take a minute^) ...
    pushd "%ROOT%\frontend"
    call npm install
    if errorlevel 1 (
        popd
        echo  [!] npm install failed. Check your internet connection and run this again.
        echo.
        pause
        exit /b 1
    )
    echo  [..] Building the dashboard ...
    call npm run build
    if errorlevel 1 (
        popd
        echo  [!] Dashboard build failed.
        echo.
        pause
        exit /b 1
    )
    popd
    echo  [OK] Dashboard built.
)

rem --- Electron (laptop GUI) ------------------------------------------
if exist "%ROOT%\node_modules\electron\package.json" (
    echo  [OK] Electron already installed - skipping.
) else (
    echo  [..] Installing Electron at repo root ...
    pushd "%ROOT%"
    call npm install
    if errorlevel 1 (
        popd
        echo  [!] npm install at repo root failed. Check your internet connection.
        echo.
        pause
        exit /b 1
    )
    popd
    echo  [OK] Electron installed.
)

rem --- Next steps -----------------------------------------------------
echo.
echo  ================================================
echo   Setup complete!
echo  ================================================
echo.
echo   Next steps:
echo    1. Put the JSymphonic program file ^(the .jar^) at:
echo         %ROOT%\backend\vendor\jsymphonic.jar
echo       Without it the app still starts, but music transfers will
echo       not work.
if exist "%ROOT%\backend\vendor\jsymphonic.jar" (
    echo       [OK] Good news: jsymphonic.jar is already there.
) else (
    echo       [!] It is not there yet.
)
echo    2. From the project folder, run:
echo         npm run electron
echo       That opens Night Ops as a laptop window (no browser tab).
echo       Legacy: double-click START-WALKMAN-BRIDGE.bat for uvicorn + browser.
echo.
pause
endlocal
