#!/bin/bash
# 把个人学习版打包成 macOS 应用，并整理成一个可直接分享的文件夹。
cd "$(dirname "$0")"

APP_NAME="DFL Coach 学习版"
OUT_DIR="打包产物-学习版"
SHARE_DIR="$OUT_DIR/分享文件夹"

if [ ! -d ".venv" ]; then
  echo "首次运行，正在创建虚拟环境……"
  python3 -m venv .venv
fi

if ! ./.venv/bin/python -c "import flask, markdown, webview, ebooklib, bs4, markdownify" >/dev/null 2>&1; then
  echo "正在安装运行依赖……"
  ./.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || \
  ./.venv/bin/python -m pip install -r requirements.txt
fi

echo "正在安装打包工具……"
./.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple pyinstaller >/dev/null 2>&1 || \
./.venv/bin/python -m pip install pyinstaller

echo "正在生成 macOS 应用，请稍候（第一次大约需要几分钟）……"
./.venv/bin/python -m PyInstaller \
  --noconfirm \
  --clean \
  --distpath "$OUT_DIR/app" \
  --workpath "$OUT_DIR/build" \
  DFL-Coach-Study.spec

if [ ! -d "$OUT_DIR/app/$APP_NAME.app" ]; then
  echo ""
  echo "打包失败：没有生成 $OUT_DIR/app/$APP_NAME.app"
  exit 1
fi

echo "正在整理分享文件夹……"
mkdir -p "$SHARE_DIR/$APP_NAME"
rm -rf "$SHARE_DIR/$APP_NAME/$APP_NAME.app"
cp -R "$OUT_DIR/app/$APP_NAME.app" "$SHARE_DIR/$APP_NAME/"
cp "个人学习指南.md" "$SHARE_DIR/$APP_NAME/"

cat > "$SHARE_DIR/$APP_NAME/先读我.txt" <<'TXT'
DFL Coach 学习版 · 使用说明

一、怎么打开
  1. 把「DFL Coach 学习版.app」拖到「应用程序」文件夹（也可以直接放在桌面）。
  2. 双击打开。第一次打开时，macOS 可能提示"无法验证开发者"：
     右键点击应用 → 选择「打开」→ 再点一次「打开」即可，
     以后就能正常双击打开了。
  3. 第一次启动会自动把四份自编讲义放进你的书架，并沿用你已配置的 API Key
     （如果这台电脑上没有配置过，打开后按提示填入即可）。

二、你的数据在哪
  ~/Library/Application Support/AiStudyCoach-Study
  里面有 books（导入的讲义）、summaries（学习总结）、sessions（对话记录）、
  mastery（复述评分与默写记录）。备份就是复制这个文件夹。

三、怎么用
  见同目录的《个人学习指南.md》：推荐每次学习先做一次「复述检测」当作基线，
  学完再测一次，配合默写与间隔复习。

四、想换回学校演示版
  那个版本包含班级、任务与教师端看板，在项目源码目录里双击「启动.command」打开。
TXT

echo "正在生成压缩包……"
rm -f "$OUT_DIR/DFL-Coach-学习版-macOS.zip"
ditto -c -k --keepParent "$SHARE_DIR/$APP_NAME" "$OUT_DIR/DFL-Coach-学习版-macOS.zip"

echo ""
echo "完成。产物都在「$OUT_DIR」里："
echo "  应用：   $OUT_DIR/app/$APP_NAME.app"
echo "  分享夹： $SHARE_DIR/$APP_NAME（含应用与使用说明）"
echo "  压缩包： $OUT_DIR/DFL-Coach-学习版-macOS.zip"
