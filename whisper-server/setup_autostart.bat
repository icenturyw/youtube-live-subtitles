@echo off
:: 设置编码为 UTF-8 避免乱码
chcp 65001 >nul
setlocal
set SCRIPT_PATH=%~dp0run_hidden.vbs
set STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_FOLDER%\YouTubeSubtitlesService.lnk

echo 正在配置开机自启动...
echo 脚本路径: %SCRIPT_PATH%
echo 启动文件夹: %STARTUP_FOLDER%

:: 使用 PowerShell 创建快捷方式，wscript.exe 后接脚本路径
powershell -NoProfile -Command "$s=(New-Object -COM WScript.Shell).CreateShortcut('%SHORTCUT_PATH%');$s.TargetPath='wscript.exe';$s.Arguments='%SCRIPT_PATH%';$s.WorkingDirectory='%~dp0';$s.Save()"

if %errorlevel% equ 0 (
    echo [成功] 已将服务添加到开机启动项。
    echo 现在您可以双击运行 run_hidden.vbs 来立即开启后台服务。
) else (
    echo [失败] 配置自启动时出错，请检查权限。
)

pause
