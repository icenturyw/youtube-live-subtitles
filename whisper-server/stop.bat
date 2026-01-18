@echo off
:: 设置编码为 UTF-8 避免乱码
chcp 65001 >nul
setlocal enabledelayedexpansion

echo 正在停止 YouTube 字幕生成服务...

:: 1. 首先尝试通过端口号 8765 查找并杀死进程
echo [1/3] 正在检查端口 8765...
set FOUND_PORT=0
for /f "tokens=5" %%a in ('netstat -aon ^| findstr :8765 ^| findstr LISTENING') do (
    echo [发现] 发现端口 8765 被 PID %%a 占用，正在终止...
    taskkill /F /PID %%a /T
    set FOUND_PORT=1
)

:: 2. 尝试执行通用的停止命令
echo [2/3] 正在强制停止 uvicorn 和 python 实例...
taskkill /F /IM uvicorn.exe /T 2>nul
taskkill /F /IM python.exe /FI "WINDOWTITLE eq YouTube Whisper Subtitle Service Startup" /T 2>nul

:: 3. 针对 main:app 这种常见的 python 进程特征进行清理（谨慎操作，仅匹配可能的 uvicorn 运行实例）
:: 这里我们通过查找命令行中包含 "main:app" 或 "8765" 的 python 进程
echo [3/3] 正在深度清理残留进程...
for /f "tokens=2 delims=," %%p in ('wmic process where "CommandLine like '%%uvicorn%%' or CommandLine like '%%main:app%%' or CommandLine like '%%8765%%'" get ProcessId /format:csv ^| findstr /r /v "^ProcessId"') do (
    set TARGET_PID=%%p
    if not "!TARGET_PID!"=="" (
        echo [发现] 发现疑似服务进程 PID !TARGET_PID!，正在终止...
        taskkill /F /PID !TARGET_PID! /T 2>nul
    )
)

echo.
echo ========================================
echo [检查] 正在验证端口状态...
netstat -aon | findstr :8765 | findstr LISTENING >nul
if %errorlevel% equ 0 (
    echo [警告] 端口 8765 仍被占用，请手动检查。
) else (
    echo [成功] 服务已彻底停止。
)
echo ========================================

timeout /t 3
