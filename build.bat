@echo off
REM ============================================================
REM  一键本地构建  ——  双击本文件即可
REM  产物在 dist_installer\
REM
REM  Python 来源优先级：
REM    1. 项目自带的 .venv\Scripts\python.exe   （推荐，uv 管理）
REM    2. py 启动器
REM    3. PATH 里的 python（自动跳过 Windows 占位符）
REM    4. 常见安装位置
REM  都没有时会提示用 uv 一键创建环境。
REM ============================================================
chcp 65001 >nul 2>&1
cd /d "%~dp0"
title 构建 记录仪视频转码工具

echo.
echo ==========================================================
echo   一键本地构建
echo ==========================================================
echo.

set "PY="

REM ---------- 1. 优先用项目自带的 .venv（uv sync 建好就一直可用）----------
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" -c "print(1)" >nul 2>&1
    if not errorlevel 1 (
        set "PY=.venv\Scripts\python.exe"
        goto :found
    )
)

REM ---------- 2. py 启动器 ----------
if not defined PY (
    where py >nul 2>&1
    if not errorlevel 1 (
        py -3 -c "import sys" >nul 2>&1
        if not errorlevel 1 (
            set "PY=py -3"
            goto :found
        )
    )
)

REM ---------- 4. 扫 PATH，跳过 WindowsApps 占位符 ----------
if not defined PY (
    for /f "delims=" %%p in ('where python 2^>nul') do (
        if not defined PY (
            echo %%p | find /i "WindowsApps" >nul
            if errorlevel 1 (
                %%p -c "print(1)" >nul 2>&1
                if not errorlevel 1 set "PY=%%p"
            )
        )
    )
)

REM ---------- 5. 常见安装位置 ----------
if not defined PY (
    for %%d in (
        "%LOCALAPPDATA%\Programs\Python\Python314\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python313\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
        "%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
        "C:\Python314\python.exe"
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

REM ---------- 6. 开发工具自带的 Python ----------
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
    echo   Windows 自带的 Microsoft\WindowsApps\python.exe 是 0 字节占位文件，
    echo   执行任何脚本都不会真正运行。
    echo.
    echo   推荐用 uv 管理（不往系统 PATH 塞任何东西）：
    echo.
    echo     winget install --id astral-sh.uv
    echo     uv sync
    echo.
    echo   之后双击本文件即可（会自动用 .venv）。
    echo.
    echo   或者安装正式版 Python：
    echo     https://www.python.org/downloads/
    echo     （安装时务必勾选 Add python.exe to PATH）
    echo.
    pause
    exit /b 1
)

echo [1/3] 使用 Python: %PY%
%PY% --version 2>&1
echo.

REM ---------- 依赖检查 ----------
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

REM ---------- 构建 ----------
echo [3/3] 开始构建（首次约 2 分钟，请勿关闭窗口）...
echo.

%PY% build_local.py %*
set "RC=%ERRORLEVEL%"

echo.
if not "%RC%"=="0" (
    echo ==========================================================
    echo   [构建失败] 退出码 %RC%
    echo ==========================================================
    echo.
    echo 如果提示缺 ffmpeg，可只出免安装版：
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