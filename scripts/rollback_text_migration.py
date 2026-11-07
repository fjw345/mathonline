"""Restore the two text columns from a pre-migration snapshot.

Only question_text/answer_text are changed. Images, versions, learning
records and answer permissions are left untouched. The script is dry-run by
default and never deletes inline assets.
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config  # noqa: E402
from backend.store import store  # noqa: E402
from backend.text_migration import project_ref, restore_text  # noqa: E402


def fail(message: str) -> int:
    print(message, file=sys.stderr)
    return 2


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("backup", type=Path)
    parser.add_argument("--apply", action="store_true", help="Actually write")
    parser.add_argument(
        "--confirm-project",
        default="",
        help="Required with --apply in Supabase mode; must match the URL project ref",
    )
    args = parser.parse_args()
    backup = json.loads(args.backup.read_text(encoding="utf-8"))
    rows = backup.get("problems", [])
    if not rows:
        return fail("备份为空，未执行。")
    ref = project_ref()
    print(
        json.dumps(
            {
                "mode": config.MODE,
                "project_ref": ref,
                "backup": str(args.backup),
                "problems": len(rows),
                "apply": args.apply,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    if args.confirm_project and ref and args.confirm_project != ref:
        return fail(
            f"项目不匹配：当前项目 {ref}，--confirm-project {args.confirm_project}。"
            "未写入任何数据。"
        )
    backup_ref = backup.get("project_ref")
    if backup.get("mode") and backup["mode"] != config.MODE:
        return fail(
            f"备份来自 {backup['mode']} 模式，当前为 {config.MODE}；未写入任何数据。"
        )
    if ref and backup_ref and ref != backup_ref:
        return fail(
            f"备份来自项目 {backup_ref}，当前项目为 {ref}；未写入任何数据。"
        )
    if config.MODE == "supabase" and not ref:
        return fail(
            "无法从 SUPABASE_URL 识别项目 ref，请检查 .env；未写入任何数据。"
        )
    if args.apply and config.MODE == "supabase" and not args.confirm_project:
        return fail(
            f"Supabase 模式写入必须显式加 --confirm-project {ref}。未写入任何数据。"
        )
    sample_id = rows[0]["id"]
    sample = store.get("problems", id=sample_id)
    if sample is None:
        return fail(f"目标库没有 {sample_id}，可能连接了错误的数据库。")
    if "question_text" not in sample:
        return fail("数据库尚未执行文字列迁移，无法恢复。")
    result = restore_text(backup, dry_run=not args.apply)
    result.update(
        {
            "mode": config.MODE,
            "project_ref": ref,
            "backup": str(args.backup),
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry-run：未写入。确认后加 --apply。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
