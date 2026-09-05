@echo off
cd /d "%~dp0"

if not exist ".venv" (
    echo 首次运行，正在创建虚拟环境……
    python -m venv .venv
)

.venv\Scripts\python -c "import flask, markdown, webview, ebooklib, bs4, markdownify, pymupdf4llm" >nul 2>&1
if errorlevel 1 (
    echo 正在安装依赖，请稍候……
    .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)

.venv\Scripts\python desktop.py
pause
