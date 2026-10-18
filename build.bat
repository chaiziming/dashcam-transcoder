@echo off
REM ============================================================
REM  一键本地构建  ——  双击本文件即可
REM  产物在 dist_installer\
REM
REM  注意：脚本会自动检查 Python 是否可用、缺什么依赖自动装。
REM  如果窗口一闪而过，请不要关闭，改用 PowerShell 运行本文件查看错误。
REM ============================================================
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title 构建 记录仪视频转码工具

echo.
echo ==========================================================
echo   一键本地构建
echo ==========================================================
echo.

REM ---------- 1. 找一个真正能用的 Python ----------
REM Windows 的 PATH 里通常有个 0 字节的 Microsoft\WindowsApps\python.exe，
REM 它执行脚本会直接返回 9009 且没有任何输出。这里必须绕开它。

set "PY="

REM 1) 先试 py 启动器（python.org 官方安装包会装）
where py >nul 2>&1
if not errorlevel 1 (
    py -3 -c "import sys" >nul 2>&1
    if not errorlevel 1 (
        set "PY=py -3"
        goto :found
    )
)

REM 2) 扫 PATH 里的 python，跳过 WindowsApps 占位
for /f "delims=" %%p in ('where python 2^>nul') do (
    if not defined PY (
        echo %%p | find /i "WindowsApps" >nul
        if errorlevel 1 (
            %%p -c "print(1)" >nul 2>&1
            if not errorlevel 1 set "PY=%%p"
        )
    )
)

REM 3) 扫常见安装位置 + 开发工具自带运行时
if not defined PY (
    for %%d in (
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "C:\Program Files\Python313\python.exe"
        "C:\Program Files\Python312\python.exe"
        "C:\Python313\python.exe"
        "C:\Python312\python.exe"
    ) do (
        if not defined PY (
            if exist %%d (
                %%d -c "print(1)" >nul 2>&1
                if not errorlevel 1 set "PY=%%~d"
            )
        )
    )
)

REM 4) 开发工具自带的 Python（如 WorkBuddy / VSCode 附带）
if not defined PY (
    for /d %%v in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
        if not defined PY (
            if exist "%%v\python.exe" (
                "%%v\python.exe" -c "print(1)" >nul 2>&1
                if not errorlevel 1 set "PY=%%v\python.exe"
            )
        )
    )
)

:found
if not defined PY (
    echo [错误] 没找到可用的 Python
    echo.
    echo   Windows 自带的 Microsoft\WindowsApps\python.exe 是 0 字节占位程序，
    echo   执行任何脚本都不会真正运行。
    echo.
    echo   请安装真正的 Python：
    echo     https://www.python.org/downloads/
    echo.
    echo   安装时务必勾选 "Add python.exe to PATH"，然后重新双击本文件。
    echo.
    pause
    exit /b 1
)

echo [1/3] 使用 Python: %PY%
%PY% --version 2>&1
echo.

REM ---------- 2. 装依赖（缺什么装什么）----------
echo [2/3] 检查依赖...

%PY% -c "import PyInstaller" >nul 2>&1
if errorlevel 1 (
    echo       正在安装 pyinstaller...
    %PY% -m pip install --upgrade pip --quiet
    %PY% -m pip install pyinstaller
    if errorlevel 1 (
        echo [错误] pyinstaller 安装失败，请检查网络
        pause
        exit /b 1
    )
)

%PY% -c "import tkinterdnd2" >nul 2>&1
if errorlevel 1 (
    echo       正在安装 tkinterdnd2...
    %PY% -m pip install tkinterdnd2
    if errorlevel 1 (
        echo [错误] tkinterdnd2 安装失败
        pause
        exit /b 1
    )
)
echo       依赖齐全
echo.

REM ---------- 3. 构建 ----------
echo [3/3] 开始构建（首次约 2 分钟，请勿关闭窗口）...
echo.

%PY% build_local.py %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo ==========================================================
    echo   [构建失败] 退出码 %RC%
    echo   详细错误见上方输出
    echo ==========================================================
    echo.
    echo 如果上面提示缺少 ffmpeg，可以改用只出免安装版的方式：
    echo   %PY% build_local.py --skip-inno
    echo.
    pause
    exit /b %RC%
)

echo ==========================================================
echo   构建完成，产物在 dist_installer\
echo ==========================================================
echo.
start "" "dist_installer"
echo 按任意键关闭...
pause >nul