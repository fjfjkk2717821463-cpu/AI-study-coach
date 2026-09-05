#!/bin/bash
cd "$(dirname "$0")"

if [ ! -d ".venv" ]; then
  echo "首次运行，正在创建虚拟环境……"
  python3 -m venv .venv
fi

if ! ./.venv/bin/python -c "import flask, markdown, webview, ebooklib, bs4, markdownify, pymupdf4llm" >/dev/null 2>&1; then
  echo "正在安装依赖，首次启动可能需要几分钟……"
  ./.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || \
  ./.venv/bin/python -m pip install -r requirements.txt
fi

exec ./.venv/bin/python desktop.py
