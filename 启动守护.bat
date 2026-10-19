@echo off
rem  ASCII only - see the long note at the top of the self-check script.
rem  Chinese text inside a .bat breaks cmd parsing on a GBK code page.
rem
rem  Note: `timeout` fails when stdin is redirected, so this script uses
rem  ping for delays instead - it works in every context.
setlocal
cd /d "%~dp0"

where pythonw >nul 2>nul
if errorlevel 1 goto nopythonw

rem  Exit code 3 = already running, 0 = free to start.
python "%~dp0wingard.py" --is-running >nul 2>nul
if errorlevel 3 goto alreadyrunning

echo Starting WinKeyGuard in the background ...
start "" pythonw "%~dp0wingard.py"
call :wait 2

echo.
python "%~dp0wingard.py" --status
echo.
echo Log file: logs\wingard.log
echo Stop it later with the stop script.
echo.
call :wait 5
exit /b 0

:alreadyrunning
echo WinKeyGuard is already running - nothing to do.
echo Use the stop script first if you want to restart it.
echo.
call :wait 5
exit /b 0

:wait
ping -n %1 127.0.0.1 >nul 2>nul
exit /b 0

:nopythonw
echo [ERROR] pythonw was not found in PATH.
echo         Install Python 3.8+ with "Add Python to PATH" ticked.
echo.
pause
exit /b 1
