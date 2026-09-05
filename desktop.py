# desktop.py
import socket
import threading
import time

import webview

import app as web_app

HOST = "127.0.0.1"


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
        "刻意摩擦学习教练",
        url,
        width=1100,
        height=780,
        min_size=(800, 600),
    )
    webview.start()


if __name__ == "__main__":
    main()
