"""Build, import and start the local application without replacing user data."""

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import threading
import urllib.request
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(command, folder=ROOT):
    subprocess.run(command, cwd=folder, check=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument(
        "--check",
        action="store_true",
        help="Build/import and verify health without starting a server",
    )
    args = parser.parse_args()
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    if sys.version_info < (3, 10):
        raise SystemExit("需要 Python 3.10 或更新版本。")
    for name in ("fastapi", "uvicorn", "httpx", "dotenv", "multipart", "PIL"):
        if not importlib.util.find_spec(name):
            run([sys.executable, "-m", "pip", "install", "-r", "requirements.txt"])
            break
    frontend = ROOT / "frontend"
    entry = frontend / "dist/index.html"
    inputs = (
        list((frontend / "src").rglob("*"))
        + list((frontend / "public").rglob("*"))
        + [
            frontend / "package.json",
            frontend / "package-lock.json",
            frontend / "vite.config.ts",
            frontend / "index.html",
        ]
    )
    if not entry.exists() or any(
        p.is_file() and p.stat().st_mtime > entry.stat().st_mtime for p in inputs
    ):
        if not (frontend / "node_modules").exists():
            run(["npm.cmd" if os.name == "nt" else "npm", "ci"], frontend)
        run(["npm.cmd" if os.name == "nt" else "npm", "run", "build"], frontend)
    from backend.config import MODE

    if MODE != "local":
        raise SystemExit(
            "此启动器用于 APP_MODE=local；云端部署请参考 docs/deployment.md。"
        )
    manifest = ROOT / "data/nonmath-manifest.json"
    if not manifest.exists():
        run([sys.executable, "-X", "utf8", "scripts/build_image_manifest.py"])
    # 本地预览默认包含非数、北交期末四科；考研题库较大，需要时再单独导入。
    manifests = [manifest]
    finals = ROOT / "data/bjtu-finals"
    if finals.exists():
        manifests += [
            finals / "calculus1-manifest.json",
            finals / "calculus2-manifest.json",
            finals / "linear-algebra-manifest.json",
            finals / "probability-manifest.json",
        ]
    for path in manifests:
        if path.exists():
            run([sys.executable, "-X", "utf8", "scripts/import_bank.py", str(path)])
    # Enrich the local preview with text without changing versions or images.
    text_manifest = ROOT / "data/text-bank-manifest.json"
    if text_manifest.exists():
        from backend.importer import apply_text_manifest

        result = apply_text_manifest(
            json.loads(text_manifest.read_text(encoding="utf-8"))
        )
        print(
            "文字版回填：更新 "
            f"{result['updated']}，跳过 {result['skipped']}，"
            f"图片已改 {result['image_mismatch']}，"
            f"本库无对应题目 {len(result['missing'])}"
        )
    # 首次导入后，用离线规则补齐知识点，保证筛选与组卷能用；有模型时可再补跑。
    from backend.store import store

    if any(not (p.get("knowledge_points") or []) for p in store.list("problems")):
        print("正在用关键词规则补齐知识点标签（离线，不调用模型）…")
        run(
            [
                sys.executable,
                "-X",
                "utf8",
                "scripts/tag_knowledge.py",
                "--no-ai",
            ]
        )
    from backend.config import AI_ENABLED, AI_KEY_SECRET, AI_MODEL_READY

    if AI_MODEL_READY and AI_KEY_SECRET:
        ai_state = "模型与密钥加密已配置；登录后在「我的学习 → AI 设置」保存自己的密钥即可使用"
    elif AI_ENABLED:
        ai_state = "已配置服务端密钥（模拟卷、变式与 AI 诊断可用；也可在 AI 设置改用个人密钥）"
    else:
        ai_state = "未配置（知识点筛选、统计可用；模拟卷/变式/AI 诊断会明确降级）"
    print("AI 功能：" + ai_state)
    if args.check:
        from fastapi.testclient import TestClient

        from backend.main import app

        client = TestClient(app)
        assert client.get("/api/v1/health").status_code == 200
        print(
            "启动检查通过，当前预览题数：",
            client.get("/api/v1/catalog").json()["total"],
        )
        return
    url = "http://127.0.0.1:8011"
    try:
        with urllib.request.urlopen(url + "/api/v1/health", timeout=2) as response:
            if b'"ok":true' in response.read():
                print("本地平台已经运行：" + url)
                if not args.no_browser:
                    webbrowser.open(url)
                return
    except OSError:
        print("正在启动本地服务…")
    print("本地平台：" + url + "\n首次注册账号自动获得管理权限。关闭此窗口可停止服务。")
    if not args.no_browser:

        def open_when_ready():
            import time

            for _ in range(30):
                try:
                    with urllib.request.urlopen(url + "/api/v1/health", timeout=1):
                        pass
                    webbrowser.open(url)
                    return
                except OSError:
                    time.sleep(0.3)

        threading.Thread(target=open_when_ready, daemon=True).start()
    import uvicorn

    uvicorn.run("backend.main:app", host="127.0.0.1", port=8011)


if __name__ == "__main__":
    try:
        main()
    except subprocess.CalledProcessError as e:
        raise SystemExit("准备步骤失败，请根据上方错误重试：" + str(e))
