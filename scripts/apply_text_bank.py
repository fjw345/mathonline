"""Backfill the optional text columns without touching the deployed bank.

Safe by default: shows what would change and writes nothing. Pass --apply to
update. A text snapshot is written before any write unless --no-backup is used.
Images, versions, learning records, unlock permissions and manual revisions are
never modified by this script.
"""

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config  # noqa: E402
from backend.importer import apply_text_manifest  # noqa: E402
from backend.store import store  # noqa: E402
from backend.text_migration import project_ref, write_snapshot  # noqa: E402


def fail(message: str, code: int = 2) -> int:
    print(message, file=sys.stderr)
    return code


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=config.ROOT / "data/text-bank-manifest.json",
    )
    parser.add_argument("--apply", action="store_true", help="Actually write")
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace non-empty text too (default preserves it)",
    )
    parser.add_argument(
        "--confirm-project",
        default="",
        help="Required with --apply in Supabase mode; must match the URL project ref",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="Snapshot path (default output/text-migration-backup-<UTC>.json)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        help="Skip the pre-write text snapshot (not recommended)",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    if not manifest.get("problems"):
        return fail("空清单，未执行。", 1)
    ref = project_ref()
    print(
        json.dumps(
            {
                "mode": config.MODE,
                "project_ref": ref,
                "manifest": str(args.manifest),
                "problems": len(manifest["problems"]),
                "apply": args.apply,
            },
            ensure_ascii=False,
        ),
        file=sys.stderr,
    )
    if args.confirm_project and ref and args.confirm_project != ref:
        return fail(
            f"项目不匹配：当前 SUPABASE_URL 项目为 {ref}，"
            f"--confirm-project 为 {args.confirm_project}。未写入任何数据。"
        )
    if config.MODE == "supabase" and not ref:
        return fail(
            "无法从 SUPABASE_URL 识别项目 ref，请检查 .env；未写入任何数据。"
        )
    if args.apply and config.MODE == "supabase" and not args.confirm_project:
        return fail(
            f"Supabase 模式写入必须显式加 --confirm-project {ref}。"
            "请先确认目标项目，再重试。未写入任何数据。"
        )
    sample_id = manifest["problems"][0]["id"]
    sample = store.get("problems", id=sample_id)
    if sample is None:
        return fail(
            f"目标库没有 {sample_id}，可能连接了错误的数据库；未写入任何数据。"
        )
    if "question_text" not in sample:
        return fail(
            "数据库缺少 question_text/problem_answers.text 列。"
            "请先在 Supabase SQL Editor 执行 "
            "supabase/migrations/202611060001_problem_text.sql；"
            "本地模式重启服务会补列。未写入任何数据。"
        )
    backup_path = None
    if args.apply and not args.no_backup:
        backup_path = args.backup or (
            config.ROOT
            / "output"
            / f"text-migration-backup-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
        )
        if backup_path.exists():
            return fail(
                f"备份文件已存在，不覆盖：{backup_path}。请换一个 --backup 路径。"
            )
        try:
            write_snapshot(backup_path)
        except OSError as exc:
            return fail(f"写入备份失败，已中止：{exc}")
        print(f"已生成迁移前文字备份：{backup_path}", file=sys.stderr)
    result = apply_text_manifest(
        manifest, only_empty=not args.overwrite, dry_run=not args.apply
    )
    result.update(
        {
            "mode": config.MODE,
            "project_ref": ref,
            "manifest": str(args.manifest),
            "overwrite": args.overwrite,
            "backup": str(backup_path) if backup_path else None,
        }
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not args.apply:
        print("dry-run：未写入。确认后加 --apply。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
