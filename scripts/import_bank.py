import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend.importer import import_manifest

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("manifest", type=Path)
    p.add_argument(
        "--override-manual",
        action="store_true",
        help="Explicitly replace admin edits for matching stable IDs",
    )
    args = p.parse_args()
    print(
        import_manifest(
            json.loads(args.manifest.read_text(encoding="utf-8")), args.override_manual
        )
    )
