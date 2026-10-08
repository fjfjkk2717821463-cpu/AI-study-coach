# desktop_study.py
"""个人学习版的桌面入口。

与 desktop.py 的区别只有一件事：在导入主程序之前先确定运行形态——
个人模式（界面收起班级与教师入口）+ 独立数据目录（不影响学校演示数据）。
"""

import os
import socket
import threading
import time

HOST = "127.0.0.1"

os.environ.setdefault(
    "AISCOACH_DATA_DIR",
    os.path.expanduser("~/Library/Application Support/AiStudyCoach-Study"),
)
os.environ["AISCOACH_PERSONAL_MODE"] = "1"

import webview

import app as web_app
import study_profile


def _find_free_port():
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((HOST, 0))
        return sock.getsockname()[1]


def _wait_for_server(port, timeout=5.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.settimeout(0.2)
            if sock.connect_ex((HOST, port)) == 0:
                return True
        time.sleep(0.1)
    return False


def main():
    data_dir = os.environ["AISCOACH_DATA_DIR"]
    default_dir = os.path.join(
        os.path.expanduser("~/Library/Application Support"), "AiStudyCoach"
    )
    study_profile.prepare(data_dir, default_data_dir=default_dir)

    port = _find_free_port()
    url = f"http://{HOST}:{port}"

    def run_server():
        web_app.app.run(
            host=HOST,
            port=port,
            debug=False,
            use_reloader=False,
            threaded=True,
        )

    server_thread = threading.Thread(target=run_server, daemon=True)
    server_thread.start()

    if not _wait_for_server(port):
        print("本地服务启动失败。")
        return

    webview.create_window(
        "DFL Coach 学习版",
        url,
        width=1100,
        height=800,
        min_size=(820, 620),
    )
    webview.start()


if __name__ == "__main__":
    main()
