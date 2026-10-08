# setup_study_profile.py
"""命令行入口：为个人学习版准备数据目录。

实现见项目根目录的 study_profile.py，打包后的应用调用的是同一份逻辑。

用法：
    AISCOACH_DATA_DIR="~/Library/Application Support/AiStudyCoach-Study" \
        python tools/setup_study_profile.py
"""

import os
import sys

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import study_profile

if __name__ == "__main__":
    study_profile.main()
