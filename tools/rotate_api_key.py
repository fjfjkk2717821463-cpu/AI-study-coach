# rotate_api_key.py
"""安全地轮换本机保存的 DeepSeek API Key。

用法：
    python tools/rotate_api_key.py --check     # 只检查当前密钥是否可用
    python tools/rotate_api_key.py             # 交互式轮换（输入新密钥）

轮换流程：显示当前密钥的掩码 → 读取新密钥（输入不回显）→ 校验格式 →
备份原文件 → 写入新密钥 → 用一次最小请求验证 → 验证失败自动回滚。
"""

import argparse
import getpass
import os
import shutil
import sys
import time

import requests

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, BASE_DIR)

import app_paths  # noqa: E402
import coach_v4  # noqa: E402


def mask(key):
    if not key:
        return "（未设置）"
    if len(key) <= 10:
        return "****"
    return f"{key[:6]}…{key[-4:]}（长度 {len(key)}）"


def env_path():
    return os.path.join(app_paths.get_data_dir(), ".env")


def read_env_lines():
    path = env_path()
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as handle:
        return handle.read().splitlines()


def write_env_key(key):
    path = env_path()
    lines = read_env_lines()
    out, replaced = [], False
    for line in lines:
        stripped = line.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            name = stripped.split("=", 1)[0].strip()
            if name == "DEEPSEEK_API_KEY":
                out.append(f"DEEPSEEK_API_KEY={key}")
                replaced = True
                continue
        out.append(line)
    if not replaced:
        if out and out[-1].strip():
            out.append("")
        out.append(f"DEEPSEEK_API_KEY={key}")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(out).rstrip("\n") + "\n")
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def verify_key(key):
    """用最小请求验证密钥可用；返回 (是否可用, 说明)。"""
    base_url, model, _ = coach_v4.get_api_settings()
    try:
        resp = requests.post(
            base_url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={
                "model": model,
                "messages": [{"role": "user", "content": "ping"}],
                "max_tokens": 1,
                "stream": False,
            },
            timeout=30,
        )
    except requests.exceptions.RequestException as exc:
        return False, f"网络异常：{exc}"
    if resp.status_code == 200:
        return True, "调用成功"
    if resp.status_code in (401, 403):
        return False, f"密钥无效或已失效（HTTP {resp.status_code}）"
    if resp.status_code == 402:
        return False, "密钥有效，但账户余额不足（HTTP 402）"
    return False, f"请求失败（HTTP {resp.status_code}）：{resp.text[:200]}"


def current_key():
    return coach_v4.load_api_key()


def do_check():
    key = current_key()
    print("当前密钥：", mask(key))
    if not key:
        print("结果：没有配置密钥，请在应用里填写或运行本工具的轮换模式。")
        return 1
    ok, message = verify_key(key)
    print("验证结果：", ("可用 ✓ " if ok else "不可用 ✖ ") + message)
    return 0 if ok else 1


def do_rotate():
    old_key = current_key()
    print("当前密钥：", mask(old_key))
    print()
    print("请在 DeepSeek 控制台（platform.deepseek.com → API keys）新建一个密钥，")
    print("先不要删除旧密钥，等这里验证通过后再回去删除。")
    print()
    new_key = getpass.getpass("粘贴新密钥（输入时不显示）：").strip()
    if not new_key:
        print("未输入内容，已取消。")
        return 1
    if not new_key.startswith("sk-") or len(new_key) < 20 or " " in new_key:
        print("格式看起来不对：DeepSeek 密钥通常以 sk- 开头且长度超过 20 位。已取消。")
        return 1
    if new_key == old_key:
        print("新密钥与当前密钥相同，无需轮换。")
        return 0
    confirm = input(f"确认写入新密钥 {mask(new_key)} ？输入 yes 继续：").strip().lower()
    if confirm != "yes":
        print("已取消。")
        return 1

    path = env_path()
    backup = f"{path}.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    if os.path.exists(path):
        shutil.copy2(path, backup)
        print(f"已备份原文件：{backup}")

    write_env_key(new_key)
    print("已写入新密钥，正在验证……")
    ok, message = verify_key(new_key)
    if ok:
        print("验证通过 ✓", message)
        print()
        print("接下来请到 DeepSeek 控制台删除旧密钥，然后重启应用（或重新打开页面）生效。")
        return 0

    print("验证失败 ✖", message)
    if os.path.exists(backup):
        shutil.copy2(backup, path)
        print("已自动回滚到原密钥，应用可继续使用。")
    else:
        print("没有备份文件，请手动在应用「模型设置」里重新填写密钥。")
    return 1


def main():
    parser = argparse.ArgumentParser(description="轮换本机保存的 DeepSeek API Key")
    parser.add_argument("--check", action="store_true", help="只检查当前密钥是否可用")
    args = parser.parse_args()
    print(f"密钥文件：{env_path()}")
    return do_check() if args.check else do_rotate()


if __name__ == "__main__":
    sys.exit(main())
