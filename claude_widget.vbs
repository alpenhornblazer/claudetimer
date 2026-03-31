Set WshShell = CreateObject("WScript.Shell")
WshShell.CurrentDirectory = "c:\Users\alpen\Cowork\claudetimer"
WshShell.Run """C:\Users\alpen\AppData\Local\Python\bin\pythonw.exe"" ""c:\Users\alpen\Cowork\claudetimer\claude_tray.py""", 0, False
