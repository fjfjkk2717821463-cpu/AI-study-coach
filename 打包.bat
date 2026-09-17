@echo off
cd /d "%~dp0"
set "SRC_DIR=%~dp0"

if not exist ".venv" (
    python -m venv .venv
)

.venv\Scripts\python -c "import flask, markdown, webview, ebooklib, bs4, markdownify, pymupdf4llm" >nul 2>&1
if errorlevel 1 (
    echo 正在安装运行依赖……
    .venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
)

echo 正在安装打包工具……
.venv\Scripts\python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller

echo 正在生成 Windows 应用，请稍候……
.venv\Scripts\python -m PyInstaller --noconfirm --clean --onefile --windowed --name "DFL Coach" --add-data "%SRC_DIR%templates;templates" --add-data "%SRC_DIR%static;static" --collect-all webview --distpath "打包产物\app" --workpath "打包产物\build" --specpath "打包产物" desktop.py

echo 正在整理分享压缩包……
if not exist "打包产物\share\DFL Coach" mkdir "打包产物\share\DFL Coach"
copy /Y "打包产物\app\DFL Coach.exe" "打包产物\share\DFL Coach\" >nul
copy /Y "使用说明.txt" "打包产物\share\DFL Coach\" >nul
powershell -Command "Compress-Archive -Path '打包产物\share\DFL Coach' -DestinationPath '打包产物\DFL-Coach-windows.zip' -Force"

echo.
echo 完成。可分享文件：
echo   - 打包产物\app\DFL Coach.exe
echo   - 打包产物\DFL-Coach-windows.zip
pause
