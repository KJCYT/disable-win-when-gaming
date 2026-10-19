@echo off
rem  ASCII only. Chinese in a .bat breaks cmd parsing (see ~/.dsh/AGENTS.md).
rem  Removes the "Open File - Security Warning" prompt by clearing the
rem  Mark-of-the-Web (Zone.Identifier) that Windows puts on downloaded files.
setlocal
cd /d "%~dp0"

echo Removing the Mark-of-the-Web from files in this folder...
powershell -NoProfile -Command "Get-ChildItem -LiteralPath '%~dp0' -Recurse -File | Unblock-File -ErrorAction SilentlyContinue"

echo.
echo Done. The security warning should no longer appear.
echo If it still appears, the prompt comes from something else -
echo see the README section about this dialog.
echo.
call :wait 5
exit /b 0

:wait
ping -n %1 127.0.0.1 >nul 2>nul
exit /b 0
