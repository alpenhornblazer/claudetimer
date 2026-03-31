@echo off
:: Check if widget is already running
tasklist /FI "WINDOWTITLE eq Claude Usage" 2>NUL | find /I "python" >NUL
if %ERRORLEVEL%==0 exit /b 0
:: Start it in the background
start /B pythonw c:\Users\alpen\Cowork\claudetimer\claude_tray.py
