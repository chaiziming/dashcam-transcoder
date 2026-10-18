@echo off
REM 一键本地构建安装包。双击本文件即可，不用记命令。
REM 产物在 dist_installer\

chcp 65001 >nul
cd /d "%~dp0"

echo.
echo 正在本地构建，请稍候...
echo   - 首次约 2 分钟
echo   - 加 --skip-exe 约 100 秒
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 找不到 python，请先安装 Python 并加入 PATH
    pause
    exit /b 1
)

python build_local.py %*
if errorlevel 1 (
    echo.
    echo [构建失败] 请查看上面的错误信息
    pause
    exit /b 1
)

echo.
echo [完成] 产物在 dist_installer\ 目录
echo.
start "" "dist_installer"
pause