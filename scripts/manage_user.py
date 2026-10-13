import argparse
import getpass
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.auth import register_local
from backend.config import MODE
from backend.store import store

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("email")
    p.add_argument("--role", choices=["admin", "moderator", "user"], default="admin")
    p.add_argument("--create", action="store_true")
    args = p.parse_args()
    user = store.get("profiles", email=args.email.lower())
    if not user and args.create and MODE == "local":
        password = getpass.getpass("Password (8+ characters): ")
        if len(password) < 8:
            raise SystemExit("Password too short")
        user = register_local(args.email.lower(), password, "管理员")
    if not user:
        raise SystemExit("Register the account first, then rerun this command.")
    user["role"] = args.role
    store.put("profiles", user)
    print("Role updated:", args.email, args.role)
