@echo off
rem  ASCII only - see the long note at the top of the self-check script.
rem  Chinese text inside a .bat breaks cmd parsing on a GBK code page.
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto nopython

echo The Win key will be blocked for 10 seconds.
echo Try pressing it - the Start menu should NOT open.
echo It is restored automatically when the timer ends.
echo.

python "%~dp0wingard.py" --test-hook 10
if errorlevel 1 goto failed

echo.
echo If the Start menu did not open, the block works.
echo.
pause
exit /b 0

:failed
echo.
echo [ERROR] Could not install the keyboard hook.
echo         Check logs\wingard.log, or try running as administrator.
echo.
pause
exit /b 1

:nopython
echo [ERROR] Python was not found in PATH.
echo.
pause
exit /b 1
