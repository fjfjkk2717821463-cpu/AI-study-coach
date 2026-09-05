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
  --name "刻意摩擦学习教练" \
  --collect-all webview \
  --distpath "打包产物/app" \
  --workpath "打包产物/build" \
  --specpath "打包产物" \
  desktop.py

echo "正在整理分享压缩包……"
mkdir -p "打包产物/share/刻意摩擦学习教练"
rm -rf "打包产物/share/刻意摩擦学习教练/刻意摩擦学习教练.app"
cp -R "打包产物/app/刻意摩擦学习教练.app" "打包产物/share/刻意摩擦学习教练/"
cp "使用说明.txt" "打包产物/share/刻意摩擦学习教练/"
rm -f "打包产物/刻意摩擦学习教练-macOS.zip"
ditto -c -k --keepParent "打包产物/share/刻意摩擦学习教练" "打包产物/刻意摩擦学习教练-macOS.zip"

echo ""
echo "完成。可分享文件都在「打包产物」文件夹："
echo "  - 打包产物/app/刻意摩擦学习教练.app"
echo "  - 打包产物/刻意摩擦学习教练-macOS.zip"
