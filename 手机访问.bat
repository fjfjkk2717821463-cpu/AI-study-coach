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

if not exist ".venv" (
    echo 首次运行，正在创建运行环境……
    python -m venv .venv
)

.venv\Scripts\python -c "import flask, markdown, webview, ebooklib, bs4, markdownify, pymupdf4llm, requests, bleach, pdfplumber, PyPDF2" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖，首次大约需要 2-5 分钟……
    .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)

set "DATA_DIR=%APPDATA%\AiStudyCoach"
if not exist "%DATA_DIR%" mkdir "%DATA_DIR%"
set "PW_FILE=%DATA_DIR%\.app_password"
if not exist "%PW_FILE%" .venv\Scripts\python -c "import secrets; print(secrets.token_urlsafe(9), end='')" > "%PW_FILE%"
set /p PW=<"%PW_FILE%"
if defined APP_PASSWORD set "PW=%APP_PASSWORD%"

echo ==============================================
echo  在手机浏览器打开下面其中一个地址：
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   http://%%a:8000
echo.
echo  访问密码：%PW%
echo.
echo  请注意：手机和这台电脑要连同一个 Wi-Fi。
echo  请保持这个窗口不要关闭。
echo ==============================================

set HOST=0.0.0.0
set PORT=8000
.venv\Scripts\python app.py
pause
