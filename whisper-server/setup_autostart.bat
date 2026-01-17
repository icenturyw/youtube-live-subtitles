@echo off
setlocal
set SCRIPT_PATH=%~dp0run_hidden.vbs
set STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_FOLDER%\YouTubeSubtitlesService.lnk

echo 正在配置开机自启动...
echo 脚本路径: %SCRIPT_PATH%
echo 启动文件夹: %STARTUP_FOLDER%

powershell -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT_PATH%');$s.TargetPath='wscript.exe';$s.Arguments='\"%SCRIPT_PATH%\"';$s.WorkingDirectory='%~dp0';$s.Save()"

if %errorlevel% equ 0 (
    echo [成功] 已将服务添加到开机启动项。
    echo 现在您可以运行 run_hidden.vbs 来立即在后台启动服务。
) else (
    echo [失败] 配置自启动时出错。
)

pause
