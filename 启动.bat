@echo off
chcp 65001 >nul
cd /d "%~dp0"

where python >nul 2>&1
if errorlevel 1 (
    echo.
    echo [没有检测到 Python]
    echo 请先安装 Python 3.9 或更高版本：https://www.python.org/downloads/
    echo 安装时务必勾选 "Add Python to PATH"，装好后重新双击本文件。
    echo.
    pause
    exit /b 1
)

title DFL Coach

if not exist ".venv" (
    echo 首次运行，正在创建运行环境……
    python -m venv .venv
    if errorlevel 1 (
        echo 创建运行环境失败，请把上面的报错截图发给开发者。
        pause
        exit /b 1
    )
)

.venv\Scripts\python -c "import flask, markdown, webview, ebooklib, bs4, markdownify, pymupdf4llm, requests, bleach, pdfplumber, PyPDF2" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖，首次大约需要 2-5 分钟，请保持联网……
    .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
    if errorlevel 1 (
        echo.
        echo [依赖安装失败] 请检查网络后重试；仍然失败可以换手机热点再试一次。
        pause
        exit /b 1
    )
)

echo 正在启动 DFL Coach，请稍候……
.venv\Scripts\python desktop.py
pause
