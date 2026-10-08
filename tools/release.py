# release.py
"""一键发版：让「版本号、更新日志、Git 标签、GitHub Release」四件事始终保持一致。

用法：
    python tools/release.py --check          # 只体检，不改动任何文件
    python tools/release.py 2.1.0            # 准备：改版本号 + 生成更新日志小节
    python tools/release.py --publish        # 发布：提交 + 打标签 + 推送 + 建 Release
    python tools/release.py 2.1.0 --publish  # 两步连做（更新日志要已经写好内容）

可选参数：
    --publish     准备好之后直接发布
    --no-push     只提交和打标签，不推送、不建 Release
    --skip-tests  跳过单元测试
    --skip-scan   跳过密钥扫描
    --dry-run     只打印将要执行的命令，不真正执行

为什么要有这个脚本：App 每次启动都会去读 GitHub 上最新的 Release，
拿标签名和自己的 `VERSION` 比较；只要两边不一致就会提示「有新版本」。
所以标签、Release、`app.py` 的版本号必须来自同一次操作。
"""

import argparse
import datetime
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_PY = os.path.join(ROOT, "app.py")
CHANGELOG = os.path.join(ROOT, "CHANGELOG.md")
REPO_URL = "https://github.com/fjfjkk2717821463-cpu/AI-study-coach"

SEMVER_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")
VERSION_RE = re.compile(r'^VERSION\s*=\s*"([^"]+)"\s*$', re.M)
VERSION_LINE_RE = re.compile(r'^(VERSION\s*=\s*)"[^"]+"(\s*)$', re.M)
UNRELEASED_RE = re.compile(r"^\[未发布\]: .*$", re.M)

DRY_RUN = False


# --------------------------------------------------------------------------- 基础工具

def say(message):
    print(message)


def die(message):
    print("")
    print("✖ " + message)
    sys.exit(1)


def run(cmd, check=True, quiet=False):
    """执行命令；quiet=True 时只在失败时输出。返回退出码。"""
    if DRY_RUN:
        print("    $ " + " ".join(cmd))
        return 0
    if not quiet:
        print("    $ " + " ".join(cmd))
    result = subprocess.run(
        cmd,
        cwd=ROOT,
        stdout=subprocess.DEVNULL if quiet else None,
        stderr=subprocess.STDOUT if quiet else None,
    )
    if check and result.returncode != 0:
        if quiet:
            print("    （上面这条命令失败了）")
        die("命令执行失败（退出码 %d）：%s" % (result.returncode, " ".join(cmd)))
    return result.returncode


def output(cmd, check=True):
    """执行命令并返回标准输出（去掉首尾空白）。"""
    if DRY_RUN:
        print("    $ " + " ".join(cmd))
        return ""
    result = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if check and result.returncode != 0:
        return ""
    return result.stdout.strip()


def python_executable():
    """优先用项目虚拟环境里的解释器，保证依赖齐全。"""
    for candidate in (os.path.join(ROOT, ".venv", "bin", "python"), os.path.join(ROOT, ".venv", "Scripts", "python.exe")):
        if os.path.exists(candidate):
            return candidate
    return sys.executable


def gh_executable():
    candidates = [
        shutil.which("gh"),
        os.path.expanduser("~/.local/bin/gh"),
        "/opt/homebrew/bin/gh",
        "/usr/local/bin/gh",
    ]
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


# --------------------------------------------------------------------------- 版本号与更新日志

def parse_version(text):
    match = SEMVER_RE.match(text.strip())
    if not match:
        die("版本号要写成 主版本.次版本.修订号，例如 2.1.0；收到的是：%r" % text)
    return tuple(int(part) for part in match.groups())


def read_version():
    with open(APP_PY, encoding="utf-8") as handle:
        text = handle.read()
    match = VERSION_RE.search(text)
    if not match:
        die("在 app.py 里找不到 VERSION = \"x.y.z\" 这一行。")
    return match.group(1)


def write_version(new_version):
    with open(APP_PY, encoding="utf-8") as handle:
        text = handle.read()
    text, count = VERSION_LINE_RE.subn(r'\g<1>"%s"\g<2>' % new_version, text, count=1)
    if count != 1:
        die("改写 app.py 的 VERSION 失败。")
    with open(APP_PY, "w", encoding="utf-8") as handle:
        handle.write(text)


def read_changelog():
    with open(CHANGELOG, encoding="utf-8") as handle:
        return handle.read()


def changelog_section(version, required=True):
    """取出更新日志里某个版本的正文（不含标题与链接引用）。"""
    text = read_changelog()
    pattern = re.compile(
        r"^## \[" + re.escape(version) + r"\][^\n]*\n(.*?)(?=^## \[|\Z)",
        re.S | re.M,
    )
    match = pattern.search(text)
    if not match:
        if required:
            die("CHANGELOG.md 里没有 v%s 这一节，先补齐再发布。" % version)
        return None
    body = "\n".join(
        line for line in match.group(1).splitlines() if not line.startswith("[")
    ).strip()
    return body


def changelog_title(version):
    text = read_changelog()
    match = re.search(r"^## \[" + re.escape(version) + r"\] - ([\d-]+)(.*)$", text, re.M)
    if not match:
        return ""
    tail = match.group(2).strip().lstrip("·-:").strip()
    return tail


def changelog_insert(version, title=""):
    """在「未发布」小节后面插入新版本小节，并更新底部的链接引用。"""
    text = read_changelog()
    if re.search(r"^## \[" + re.escape(version) + r"\]", text, re.M):
        say("更新日志里已经有 v%s 小节了，跳过生成。" % version)
        return

    heading = "## [%s] - %s" % (version, datetime.date.today().isoformat())
    if title:
        heading += " · " + title
    block = (
        heading + "\n\n"
        "<!-- 写清楚这一版做了什么，再删掉这行注释。分类可选：新增 / 变更 / 修复 / 安全 -->\n\n"
        "### 新增\n\n- \n\n"
        "### 变更\n\n- \n\n"
        "### 修复\n\n- \n\n"
    )

    marker = "## [未发布]\n\n"
    if marker not in text:
        die("CHANGELOG.md 里找不到「## [未发布]」小节，无法自动插入。")
    index = text.index(marker) + len(marker)
    text = text[:index] + block + text[index:]

    previous = read_version()
    text = UNRELEASED_RE.sub(
        "[未发布]: %s/compare/v%s...HEAD" % (REPO_URL, version), text, count=1
    )
    text = text.replace(
        "[未发布]: %s/compare/v%s...HEAD" % (REPO_URL, version),
        "[未发布]: %s/compare/v%s...HEAD\n[%s]: %s/compare/v%s...v%s"
        % (REPO_URL, version, version, REPO_URL, previous, version),
        1,
    )

    with open(CHANGELOG, "w", encoding="utf-8") as handle:
        handle.write(text)
    say("已在 CHANGELOG.md 里生成 v%s 小节，请补充内容后再发布。" % version)


# --------------------------------------------------------------------------- 各项检查

def check_tree_clean():
    dirty = output(["git", "status", "--porcelain"])
    if dirty:
        return False, dirty.splitlines()
    return True, []


def run_tests():
    say("\n== 单元测试 ==")
    run([python_executable(), "-m", "unittest", "discover", "-s", "tests"], quiet=True)
    say("   测试全部通过 ✓")


def run_secret_scan():
    say("\n== 密钥扫描（Git 全量历史）==")
    run([python_executable(), "tools/secret_scan.py", "--history", "--quiet"])
    say("   历史里没有发现真实密钥 ✓")


def report_checks():
    version = read_version()
    say("版本号（app.py）      ：%s" % version)
    parse_version(version)

    tag = output(["git", "describe", "--tags", "--abbrev=0"], check=False)
    say("最近一个标签          ：%s" % (tag or "（还没有标签）"))

    problems = []
    if tag != "v" + version:
        problems.append(
            "app.py 的版本号是 %s，最近的标签是 %s —— 发布后两者必须一致。"
            % (version, tag or "无")
        )

    if changelog_section(version, required=False) is None:
        problems.append("CHANGELOG.md 里缺少 v%s 小节。" % version)

    dirty = output(["git", "status", "--porcelain"])
    if dirty:
        say("工作区                ：有未提交的改动（%d 项）" % len(dirty.splitlines()))
    else:
        say("工作区                ：干净")

    say("")
    if problems:
        for item in problems:
            say("⚠ " + item)
    else:
        say("版本号、标签与更新日志一致 ✓")
    return 1 if problems else 0


# --------------------------------------------------------------------------- 三个动作

def action_prepare(new_version, title=""):
    say("\n== 准备 v%s ==" % new_version)
    current = read_version()
    if parse_version(new_version) <= parse_version(current):
        die("新版本号要比当前版本 %s 大。" % current)

    clean, dirty = check_tree_clean()
    if not clean:
        die(
            "工作区还有未提交的改动，先把它们提交掉再发版：\n  " + "\n  ".join(dirty[:10])
        )

    write_version(new_version)
    changelog_insert(new_version, title=title)

    say("")
    say("下一步：")
    say("  1. 打开 CHANGELOG.md，把 v%s 这一节写完（说明这一版做了什么）" % new_version)
    say("  2. 运行 python tools/release.py --publish")


def action_publish(no_push=False, skip_tests=False, skip_scan=False):
    version = read_version()
    tag = "v" + version
    say("\n== 发布 %s ==" % tag)

    body = changelog_section(version)
    if not re.search(r"^\s*-\s+\S", body, re.M):
        die("CHANGELOG.md 里 v%s 这一节还是空的，先写清楚这一版做了什么。" % version)

    existing = output(["git", "tag", "--list", tag])
    if existing:
        die("标签 %s 已经存在。如果确实要重发，先删除：git tag -d %s" % (tag, tag))

    if not skip_tests:
        run_tests()
    if not skip_scan:
        run_secret_scan()

    say("\n== 提交与打标签 ==")
    run(["git", "add", "app.py", "CHANGELOG.md"])
    run(["git", "commit", "-m", "chore(release): %s" % tag])
    run(["git", "tag", "-a", tag, "-m", tag])

    if no_push:
        say("\n已提交并打好标签（未推送）。推送命令：")
        say("    git push origin HEAD --follow-tags")
        return

    say("\n== 推送与创建 Release ==")
    run(["git", "push", "origin", "HEAD", "--follow-tags"])

    gh = gh_executable()
    if not gh:
        say("没有找到 gh 命令，请到 GitHub 网页手动创建 Release：%s/releases/new?tag=%s" % (REPO_URL, tag))
        return

    release_title = tag
    subtitle = changelog_title(version)
    if subtitle:
        release_title = "%s %s" % (tag, subtitle)

    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False, encoding="utf-8") as handle:
        handle.write(body + "\n")
        notes_path = handle.name
    try:
        run([gh, "release", "create", tag, "--title", release_title, "--notes-file", notes_path])
    finally:
        os.unlink(notes_path)

    say("")
    say("发布完成：%s/releases/tag/%s" % (REPO_URL, tag))


# --------------------------------------------------------------------------- 入口

def main():
    parser = argparse.ArgumentParser(
        description="一键发版：版本号、更新日志、标签与 Release 保持一致",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("version", nargs="?", help="要发布的新版本号，例如 2.1.0")
    parser.add_argument("--title", default="", help="更新日志小节标题，例如「教师端增强」")
    parser.add_argument("--check", action="store_true", help="只体检，不改动任何文件")
    parser.add_argument("--publish", action="store_true", help="提交、打标签、推送并创建 Release")
    parser.add_argument("--no-push", action="store_true", help="只提交和打标签，不推送")
    parser.add_argument("--skip-tests", action="store_true", help="跳过单元测试")
    parser.add_argument("--skip-scan", action="store_true", help="跳过密钥扫描")
    parser.add_argument("--dry-run", action="store_true", help="只打印将要执行的命令")
    args = parser.parse_args()

    global DRY_RUN
    DRY_RUN = args.dry_run

    os.chdir(ROOT)

    if args.check:
        code = report_checks()
        if not args.skip_tests:
            run_tests()
        if not args.skip_scan:
            run_secret_scan()
        sys.exit(code)

    if args.version:
        action_prepare(args.version, title=args.title)

    if args.publish:
        action_publish(no_push=args.no_push, skip_tests=args.skip_tests, skip_scan=args.skip_scan)

    if not args.version and not args.publish:
        parser.print_help()


if __name__ == "__main__":
    main()
