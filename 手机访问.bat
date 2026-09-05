@echo off
cd /d "%~dp0"

if not exist ".venv" (
    python -m venv .venv
)

.venv\Scripts\python -c "import flask, markdown, ebooklib, bs4, markdownify, pymupdf4llm" >nul 2>&1
if errorlevel 1 (
    .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)

echo ==============================================
echo  在手机浏览器打开下面其中一个地址：
echo.
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do echo   http://%%a:8000
echo.
echo  请保持这个窗口不要关闭。
echo ==============================================

set HOST=0.0.0.0
set PORT=8000
.venv\Scripts\python app.py
pause
