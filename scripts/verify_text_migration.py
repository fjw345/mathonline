"""Read-only verification of the text migration.

Checks columns, manifest coverage, text/image references, inline assets and
storage objects. It never writes to the database or storage. Use --strict to
also fail on text or image differences (for example after an admin edit).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import config  # noqa: E402
from backend.text_migration import project_ref, verify_text  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "manifest",
        nargs="?",
        type=Path,
        default=config.ROOT / "data/text-bank-manifest.json",
    )
    parser.add_argument(
        "--backup",
        type=Path,
        default=None,
        help="Optional pre-migration snapshot; verifies versions/updated_at did not change",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Also fail on text/image differences",
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=None,
        help="Write the JSON report to this file",
    )
    args = parser.parse_args()
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    backup = (
        json.loads(args.backup.read_text(encoding="utf-8")) if args.backup else None
    )
    report = verify_text(manifest, backup=backup, strict=args.strict)
    report["manifest"] = str(args.manifest)
    report["backup"] = str(args.backup) if args.backup else None
    report["project_ref"] = project_ref()
    text = json.dumps(report, ensure_ascii=False, indent=2)
    print(text)
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(text + "\n", encoding="utf-8")
    if not report["ok"]:
        print("验证未通过，见上方 JSON。", file=sys.stderr)
        return 1
    print("验证通过。", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
