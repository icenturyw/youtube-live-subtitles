@echo off
:: 设置编码为 UTF-8 避免乱码
chcp 65001 >nul
setlocal
set STARTUP_FOLDER=%APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup
set SHORTCUT_PATH=%STARTUP_FOLDER%\YouTubeSubtitlesService.lnk

echo 正在关闭开机自启动...

if exist "%SHORTCUT_PATH%" (
    del "%SHORTCUT_PATH%"
    if %errorlevel% equ 0 (
        echo [成功] 已从开机启动项中移除。
    ) else (
        echo [失败] 移除自启动项时出错，请检查权限。
    )
) else (
    echo [信息] 未找到开机启动项，可能已经关闭。
)

pause
