#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

if ! ./.venv/bin/python -c "import flask, markdown, webview, ebooklib, bs4, markdownify, pymupdf4llm" >/dev/null 2>&1; then
  echo "正在安装运行依赖……"
  ./.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || \
  ./.venv/bin/python -m pip install -r requirements.txt
fi

echo "正在安装打包工具……"
./.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller || \
./.venv/bin/python -m pip install pyinstaller

echo "正在生成 macOS 应用，请稍候……"
./.venv/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --windowed \
  --name "DFL Coach" \
  --add-data "templates:templates" \
  --add-data "static:static" \
  --collect-all webview \
  --distpath "打包产物/app" \
  --workpath "打包产物/build" \
  --specpath "打包产物" \
  desktop.py

echo "正在整理分享压缩包……"
mkdir -p "打包产物/share/DFL Coach"
rm -rf "打包产物/share/DFL Coach/DFL Coach.app"
cp -R "打包产物/app/DFL Coach.app" "打包产物/share/DFL Coach/"
cp "使用说明.txt" "打包产物/share/DFL Coach/"
rm -f "打包产物/DFL-Coach-macOS.zip"
ditto -c -k --keepParent "打包产物/share/DFL Coach" "打包产物/DFL-Coach-macOS.zip"

echo ""
echo "完成。可分享文件都在「打包产物」文件夹："
echo "  - 打包产物/app/DFL Coach.app"
echo "  - 打包产物/DFL-Coach-macOS.zip"
