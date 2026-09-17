# secret_scan.py
"""密钥与敏感信息扫描：工作区、提交暂存区、打包产物、Git 全量历史。

用途：
1. 手动复查：`python tools/secret_scan.py --all`
2. 提交前自动拦截：由 `.githooks/pre-commit` 调用 `--staged`
3. 持续集成：在 CI 里运行 `--history --strict`，有命中即失败

退出码：0 = 通过；1 = 发现高危项（可用于拦截提交）；2 = 参数错误。
"""

import argparse
import os
import re
import subprocess
import sys
import zipfile

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 高危：一旦出现通常就是真实泄露
CRITICAL_PATTERNS = [
    ("API 密钥（sk- 开头）", re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}")),
    ("GitHub 令牌", re.compile(r"\b(?:ghp|gho|ghs|ghr)_[A-Za-z0-9]{20,}")),
    ("GitHub 细粒度令牌", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}")),
    ("OpenAI 组织密钥", re.compile(r"\bsk-(?:proj|org)-[A-Za-z0-9_\-]{20,}")),
    ("Google API Key", re.compile(r"\bAIza[0-9A-Za-z_\-]{30,}")),
    ("Slack 令牌", re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{10,}")),
    ("私钥文件内容", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----")),
    (
        "密钥赋值",
        re.compile(
            r"(?i)\b(?:api[_-]?key|apikey|secret[_-]?key|access[_-]?token|auth[_-]?token|password|passwd)\b"
            r"\s*[:=]\s*[\"'][^\"'\s]{12,}[\"']"
        ),
    ),
    ("Bearer 令牌", re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._\-]{24,}")),
]

# 提醒：可能是个人信息，通常不阻断提交，但值得人工确认
WARNING_PATTERNS = [
    ("手机号", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")),
    ("身份证号", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)")),
    ("邮箱", re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}")),
]

TEXT_EXT = {
    ".py", ".md", ".txt", ".json", ".html", ".js", ".css", ".yml", ".yaml",
    ".toml", ".ini", ".cfg", ".sh", ".command", ".bat", ".ps1", ".spec",
    ".plist", ".example", ".env", ".sql", ".xml",
}
SKIP_DIRS = {".git", ".venv", "venv", "__pycache__", "node_modules", ".idea", "build", "dist"}

# 允许列表：命中这些上下文不算泄露
ALLOWLIST = [
    re.compile(r"sk-你的"),                      # .env.example 里的占位符
    re.compile(r"sk-demo-key"),                  # 演示环境用的假密钥
    re.compile(r"@(?:example|test|localhost)\.", re.I),
    re.compile(r"users\.noreply\.github\.com"),  # GitHub 匿名邮箱
]
# 目录级的提醒降噪：第三方库的许可证文件里必然有作者邮箱
WARNING_SKIP_DIRS = ("打包产物", "旧版本存档", "docs/screenshots")


def is_text_file(path):
    return os.path.splitext(path)[1].lower() in TEXT_EXT or os.path.basename(path) in {
        ".env", ".env.example", ".gitignore",
    }


def allowed(line):
    return any(pattern.search(line) for pattern in ALLOWLIST)


def scan_text(text, path, critical, warnings):
    for lineno, line in enumerate(text.splitlines(), 1):
        if allowed(line):
            continue
        for name, pattern in CRITICAL_PATTERNS:
            if pattern.search(line):
                critical.append((path, lineno, name, line.strip()[:120]))
        for name, pattern in WARNING_PATTERNS:
            if pattern.search(line):
                warnings.append((path, lineno, name, line.strip()[:120]))


def scan_working_tree(root, critical, warnings):
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if not is_text_file(path):
                continue
            try:
                text = open(path, encoding="utf-8", errors="ignore").read()
            except OSError:
                continue
            scanned += 1
            if any(part in rel for part in WARNING_SKIP_DIRS):
                only = []
                scan_text(text, rel, critical, only)
            else:
                scan_text(text, rel, critical, warnings)
    return scanned


def scan_staged(root, critical, warnings):
    """只扫描本次提交暂存的内容，供 pre-commit 钩子使用。"""
    files = subprocess.run(
        ["git", "diff", "--cached", "--name-only", "--diff-filter=ACM"],
        cwd=root, capture_output=True, text=True,
    ).stdout.splitlines()
    scanned = 0
    for rel in files:
        if not is_text_file(rel):
            continue
        content = subprocess.run(
            ["git", "show", ":" + rel], cwd=root, capture_output=True, text=True,
        )
        if content.returncode != 0:
            continue
        scanned += 1
        scan_text(content.stdout, rel, critical, warnings)
    return scanned


def scan_packages(root, critical, warnings):
    """扫描打包产物（压缩包内部也要看，因为压缩包会被分发出去）。"""
    scanned = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in {".git", ".venv", "node_modules"}]
        for name in filenames:
            path = os.path.join(dirpath, name)
            rel = os.path.relpath(path, root)
            if name.lower().endswith(".zip"):
                try:
                    with zipfile.ZipFile(path) as archive:
                        for info in archive.infolist():
                            if info.is_dir() or not is_text_file(info.filename):
                                continue
                            try:
                                text = archive.read(info).decode("utf-8", errors="ignore")
                            except Exception:
                                continue
                            scanned += 1
                            scan_text(text, f"{rel}::{info.filename}", critical, [])
                except zipfile.BadZipFile:
                    continue
            elif is_text_file(path) and any(part in rel for part in ("打包产物", "release")):
                try:
                    text = open(path, encoding="utf-8", errors="ignore").read()
                except OSError:
                    continue
                scanned += 1
                scan_text(text, rel, critical, [])
    return scanned


def scan_history(root, critical, warnings):
    """扫描 Git 全量历史里出现过的所有内容。"""
    revs = subprocess.run(
        ["git", "rev-list", "--all"], cwd=root, capture_output=True, text=True,
    ).stdout.split()
    scanned = 0
    for rev in revs:
        files = subprocess.run(
            ["git", "ls-tree", "-r", "--name-only", rev],
            cwd=root, capture_output=True, text=True,
        ).stdout.splitlines()
        for rel in files:
            if not is_text_file(rel):
                continue
            blob = subprocess.run(
                ["git", "show", f"{rev}:{rel}"], cwd=root, capture_output=True, text=True,
            )
            if blob.returncode != 0:
                continue
            scanned += 1
            scan_text(blob.stdout, f"{rev[:8]}:{rel}", critical, warnings)
    return scanned


def report(title, critical, warnings, scanned):
    print(f"\n{'=' * 10} {title}（扫描 {scanned} 个文本单元）{'=' * 10}")
    if not critical and not warnings:
        print("  未发现可疑内容 ✓")
        return
    for path, lineno, name, sample in critical:
        print(f"  [高危] {path}:{lineno} {name} → {sample}")
    for path, lineno, name, sample in warnings:
        print(f"  [提醒] {path}:{lineno} {name} → {sample}")


def main():
    parser = argparse.ArgumentParser(description="密钥与敏感信息扫描")
    parser.add_argument("--tree", action="store_true", help="扫描工作区（默认）")
    parser.add_argument("--staged", action="store_true", help="只扫描本次提交的暂存内容")
    parser.add_argument("--packages", action="store_true", help="扫描打包产物（含压缩包内部）")
    parser.add_argument("--history", action="store_true", help="扫描 Git 全量历史")
    parser.add_argument("--all", action="store_true", help="以上全部")
    parser.add_argument("--strict", action="store_true", help="把提醒项也视为失败")
    parser.add_argument("--quiet", action="store_true", help="只在发现问题时输出")
    args = parser.parse_args()

    if not any([args.tree, args.staged, args.packages, args.history, args.all]):
        args.tree = True

    total_critical = []
    total_warnings = []
    tasks = []
    if args.all or args.tree:
        tasks.append(("工作区", scan_working_tree, BASE_DIR))
    if args.all or args.staged:
        tasks.append(("暂存内容", scan_staged, BASE_DIR))
    if args.all or args.packages:
        tasks.append(("打包产物", scan_packages, BASE_DIR))
    if args.all or args.history:
        tasks.append(("Git 历史", scan_history, BASE_DIR))

    for title, func, root in tasks:
        critical, warnings = [], []
        scanned = func(root, critical, warnings)
        if not args.quiet or critical or warnings:
            report(title, critical, warnings, scanned)
        total_critical.extend(critical)
        total_warnings.extend(warnings)

    print()
    if total_critical:
        print(f"结论：发现 {len(total_critical)} 项高危内容，请先处理再提交。")
        return 1
    if total_warnings:
        print(f"结论：无高危内容；有 {len(total_warnings)} 项提醒，建议人工确认。")
        return 1 if args.strict else 0
    print("结论：未发现密钥或敏感信息。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
