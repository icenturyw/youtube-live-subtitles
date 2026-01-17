Set WshShell = CreateObject("WScript.Shell")
' 获取此脚本所在的目录
strPath = CreateObject("Scripting.FileSystemObject").GetParentFolderName(WScript.ScriptFullName)
' 切换到该目录并运行 start.bat，0 表示不显示窗口，False 表示不用等待程序结束
WshShell.CurrentDirectory = strPath
WshShell.Run "cmd /c start.bat", 0, False
