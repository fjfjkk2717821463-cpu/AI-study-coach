#!/bin/bash
# 个人学习版启动脚本
# 与学校演示版互不干扰：数据放在独立目录，界面收起班级与教师入口。
cd "$(dirname "$0")"

export AISCOACH_DATA_DIR="$HOME/Library/Application Support/AiStudyCoach-Study"
export AISCOACH_PERSONAL_MODE=1
export PORT="${PORT:-8000}"

if [ ! -d ".venv" ]; then
  echo "首次运行，正在创建虚拟环境……"
  python3 -m venv .venv
fi

if ! ./.venv/bin/python -c "import flask, markdown, ebooklib, bs4, markdownify" >/dev/null 2>&1; then
  echo "正在安装依赖，首次启动可能需要几分钟……"
  ./.venv/bin/python -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt || \
  ./.venv/bin/python -m pip install -r requirements.txt
fi

./.venv/bin/python tools/setup_study_profile.py

echo ""
echo "学生端： http://127.0.0.1:$PORT"
echo "按 Control + C 结束学习。"
echo ""

open "http://127.0.0.1:$PORT" >/dev/null 2>&1 &
exec ./.venv/bin/python app.py
