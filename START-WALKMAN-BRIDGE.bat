@echo off
setlocal
rem ================================================================
rem  WALKMAN BRIDGE - start the server and open the dashboard
rem  Double-click me. Close this window to stop the server.
rem ================================================================
set "ROOT=%~dp0"

rem --- has setup been run? --------------------------------------------
if not exist "%ROOT%backend\.venv\Scripts\python.exe" (
    echo.
    echo  [!] The app is not set up yet ^(backend\.venv is missing^).
    echo      Please double-click scripts\setup.bat first, then try again.
    echo.
    pause
    exit /b 1
)
if not exist "%ROOT%frontend\dist\index.html" (
    echo.
    echo  [!] The dashboard is not built yet ^(frontend\dist is missing^).
    echo      Please double-click scripts\setup.bat first, then try again.
    echo.
    pause
    exit /b 1
)

rem --- resolve Java: env override, portable JDK in ..\tools, then PATH
set "JAVA_EXE="
if defined WALKMAN_BRIDGE_JAVA set "JAVA_EXE=%WALKMAN_BRIDGE_JAVA%"
if not defined JAVA_EXE (
    for /d %%J in ("%ROOT%..\tools\jdk*") do (
        if exist "%%~fJ\bin\java.exe" set "JAVA_EXE=%%~fJ\bin\java.exe"
    )
)
if not defined JAVA_EXE (
    where java >nul 2>nul
    if not errorlevel 1 set "JAVA_EXE=java"
)
if defined JAVA_EXE (
    set "WALKMAN_BRIDGE_JAVA=%JAVA_EXE%"
    echo  [i] Java: %JAVA_EXE%
) else (
    echo  [!] No Java found. Music transfers to the Walkman will not work.
    echo      Install a JDK ^(e.g. Temurin 21^) or place a portable one in
    echo      the tools\jdk... folder next to this project.
)

rem --- warn if the JSymphonic jar is missing --------------------------
if not exist "%ROOT%backend\vendor\jsymphonic.jar" (
    echo.
    echo  [!] backend\vendor\jsymphonic.jar is missing.
    echo      The dashboard will still open and show your Walkman's status,
    echo      but music transfers will NOT work until the jar is added.
    echo      See scripts\setup.bat for details.
    echo.
)

rem --- launch ---------------------------------------------------------
echo  [i] Starting Walkman Bridge at http://127.0.0.1:8000 ...
echo      Your browser will open in a moment.
echo      Keep this window open while you use the app.
echo      Close it ^(or press Ctrl+C^) to stop the server.
echo.
start "" /min cmd /c "ping -n 3 127.0.0.1 >nul & start http://127.0.0.1:8000"
cd /d "%ROOT%backend"
call "%ROOT%backend\.venv\Scripts\activate.bat"
"%ROOT%backend\.venv\Scripts\python.exe" -m uvicorn main:app --host 127.0.0.1 --port 8000
echo.
echo  [i] Server stopped.
pause
endlocal
