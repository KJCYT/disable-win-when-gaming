@echo off
rem  ASCII only. Chinese in a .bat breaks cmd parsing (see ~/.dsh/AGENTS.md).
rem  Exit codes are meaningful: 0 = stopped or already stopped,
rem  1 = python missing, 2 = stop actually failed.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto nopython

python "%~dp0wingard.py" --is-running >nul 2>nul
if errorlevel 3 goto stopping

echo Nothing is running right now - the Win key is already normal.
echo.
call :wait 4
exit /b 0

:stopping
python "%~dp0wingard.py" --stop
if errorlevel 1 goto stopfailed
call :wait 3
exit /b 0

:stopfailed
echo.
echo [WARN] The stop request reported a problem. Check logs\wingard.log.
echo        If the Win key still misbehaves, reboot or end pythonw.exe manually.
echo.
call :wait 5
exit /b 2

:wait
ping -n %1 127.0.0.1 >nul 2>nul
exit /b 0

:nopython
echo [ERROR] Python was not found in PATH.
echo.
pause
exit /b 1
