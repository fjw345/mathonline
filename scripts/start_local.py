"""Build, import and start the local application without replacing user data."""

import argparse
import importlib.util
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
    run([sys.executable, "-X", "utf8", "scripts/import_bank.py", str(manifest)])
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
