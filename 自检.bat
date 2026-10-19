@echo off
rem ---------------------------------------------------------------------
rem  This file is intentionally pure ASCII.
rem  cmd.exe parses .bat files using the system code page (usually 936/GBK).
rem  A UTF-8 saved .bat gets mangled line by line before chcp can take
rem  effect, which turns Chinese text into bogus commands. All Chinese
rem  messages are printed by wingard.py instead, which writes correctly
rem  to any console code page.
rem ---------------------------------------------------------------------
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto nopython

python "%~dp0wingard.py" --diagnose
if errorlevel 1 goto failed

echo.
python "%~dp0wingard.py" --once

echo.
echo ------------------------------------------------------------
echo  If it says the Win key stays on while your game is running,
echo  add the game process name with:  wingard.py --add
echo ------------------------------------------------------------
echo.
pause
exit /b 0

:nopython
echo [ERROR] Python was not found in PATH.
echo         Install Python 3.8+ and make sure "python" works in cmd.
echo.
pause
exit /b 1

:failed
echo.
echo [ERROR] Self-check failed. See the message above, or read:
echo         %~dp0logs\wingard.log
echo.
pause
exit /b 1
