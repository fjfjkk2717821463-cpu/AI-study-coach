#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi

if ! ./.venv/bin/python -c "import flask, markdown, ebooklib, bs4, markdownify, pymupdf4llm" >/dev/null 2>&1; then
  ./.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || \
  ./.venv/bin/python -m pip install -r requirements.txt
fi

IP=$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1")
PORT=8000

DATA_DIR="$HOME/Library/Application Support/AiStudyCoach"
mkdir -p "$DATA_DIR"
PW_FILE="$DATA_DIR/.app_password"
if [ -n "$APP_PASSWORD" ]; then
  PW="$APP_PASSWORD"
else
  if [ ! -f "$PW_FILE" ]; then
    ./.venv/bin/python -c "import secrets; print(secrets.token_urlsafe(9), end='')" > "$PW_FILE"
  fi
  PW=$(cat "$PW_FILE")
fi

echo "=============================================="
echo " 在 iPhone 的 Safari 中打开下面的地址："
echo ""
echo "   http://${IP}:${PORT}"
echo ""
echo " 访问密码：${PW}"
echo ""
echo " 请保持这个窗口不要关闭。"
echo "=============================================="

HOST=0.0.0.0 PORT=$PORT exec ./.venv/bin/python app.py
