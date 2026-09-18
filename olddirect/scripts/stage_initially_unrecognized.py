"""Stage first-pass extraction failures and write manual-review manifests.

The source files are copied, not moved, so existing database source paths and
the reproducible import pipeline remain valid.
"""
from __future__ import annotations

import csv
import json
import shutil
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RESOURCE = ROOT / "resource"
STAGE = ROOT / "待处理" / "首次未识别资源"


def slash(path: Path) -> str:
    return path.as_posix()


def write_csv(path: Path, fieldnames: list[str], rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    import_report = json.loads((DATA / "import_report.json").read_text(encoding="utf-8"))
    source_by_id = {row["id"]: row for row in import_report["sources"]}
    source_by_path = {row["path"]: row for row in import_report["sources"]}

    first_pass: list[dict[str, object]] = []

    # Every Word sidecar corresponds to one of the 52 legacy .doc files that
    # the first pass could not read.
    for sidecar in sorted((DATA / "word_text").rglob("*.txt")):
        relative_sidecar = sidecar.relative_to(DATA / "word_text")
        relative_source = Path(str(relative_sidecar)[: -len(".txt")])
        source = RESOURCE / relative_source
        first_pass.append(
            {
                "category": "旧版DOC（首轮无文本）",
                "source": source,
                "sidecar": sidecar,
            }
        )

    # Every OCR sidecar corresponds to one of the 19 image-only PDFs that the
    # first pass could not parse into questions.
    for sidecar in sorted((DATA / "ocr_text").rglob("*.json")):
        payload = json.loads(sidecar.read_text(encoding="utf-8"))
        source = ROOT / Path(payload["source"])
        first_pass.append(
            {
                "category": "扫描PDF（首轮无可用文本）",
                "source": source,
                "sidecar": sidecar,
            }
        )

    db = sqlite3.connect(DATA / "problem_bank.sqlite3")
    link_counts = Counter(dict(db.execute(
        "SELECT source_id, COUNT(*) FROM problem_source_links GROUP BY source_id"
    )))

    manifest_rows: list[dict[str, object]] = []
    missing: list[str] = []
    copied_bytes = 0
    for item in first_pass:
        source = Path(item["source"])
        sidecar = Path(item["sidecar"])
        if not source.exists():
            missing.append(slash(source.relative_to(ROOT)))
            continue

        source_relative = source.relative_to(RESOURCE)
        staged_source = STAGE / "原始文件" / source_relative
        staged_source.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, staged_source)
        copied_bytes += source.stat().st_size

        sidecar_relative = sidecar.relative_to(DATA)
        staged_sidecar = STAGE / "提取文本" / sidecar_relative
        staged_sidecar.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(sidecar, staged_sidecar)

        report_path = slash(source.relative_to(ROOT))
        report_row = source_by_path.get(report_path)
        source_id = report_row["id"] if report_row else ""
        manifest_rows.append(
            {
                "类别": item["category"],
                "当前状态": "已通过二次提取恢复，仍需人工核对公式",
                "当前关联题目数": link_counts[source_id],
                "原始路径": report_path,
                "集中副本路径": slash(staged_source.relative_to(ROOT)),
                "提取文本路径": slash(staged_sidecar.relative_to(ROOT)),
            }
        )

    write_csv(
        STAGE / "首次未识别资源清单.csv",
        ["类别", "当前状态", "当前关联题目数", "原始路径", "集中副本路径", "提取文本路径"],
        manifest_rows,
    )

    issue_rows: list[dict[str, object]] = []
    issue_summary: Counter[tuple[str, str, str]] = Counter()
    for source_id, page, issue_type, details in db.execute(
        "SELECT source_id, page, issue_type, details FROM extraction_issues "
        "ORDER BY source_id, page, id"
    ):
        source = source_by_id.get(source_id, {})
        source_path = source.get("path", "")
        issue_rows.append(
            {
                "源文件": source_path,
                "页码或块起始页": page if page is not None else "",
                "问题类型": issue_type,
                "未采用的识别片段": details,
            }
        )
        issue_summary[(source_id, source_path, issue_type)] += 1
    db.close()

    write_csv(
        STAGE / "当前残留识别问题_逐条.csv",
        ["源文件", "页码或块起始页", "问题类型", "未采用的识别片段"],
        issue_rows,
    )
    summary_rows = [
        {"源文件": path, "问题类型": issue_type, "残留片段数": count}
        for (_source_id, path, issue_type), count in sorted(
            issue_summary.items(), key=lambda item: (-item[1], item[0][1])
        )
    ]
    write_csv(
        STAGE / "当前残留识别问题_按文件汇总.csv",
        ["源文件", "问题类型", "残留片段数"],
        summary_rows,
    )

    category_counts = Counter(row["类别"] for row in manifest_rows)
    readme = f"""# 首次未识别资源待处理区

这里集中保存了首轮未能抽取的源文件副本，共 {len(manifest_rows)} 份：

- 旧版 DOC：{category_counts['旧版DOC（首轮无文本）']} 份
- 扫描 PDF：{category_counts['扫描PDF（首轮无可用文本）']} 份

这些文件后来已通过 Apache Tika 或中文 OCR 重新抽取，因此不是“当前完全无法识别”；但公式、矩阵、上下标和选项仍可能存在识别误差，适合寻找更清晰的 PDF/DOCX 替代版本。

## 清单说明

- `首次未识别资源清单.csv`：这 {len(manifest_rows)} 份文件的精确路径、当前恢复状态和入库关联数。
- `当前残留识别问题_按文件汇总.csv`：当前 {len(issue_summary)} 个“源文件/问题类型”组合的统计。
- `当前残留识别问题_逐条.csv`：数据库中全部 {len(issue_rows)} 条未采用短片段，含页码和识别文本。
- `原始文件/`：首轮未识别源文件的集中副本，保留原目录结构。
- `提取文本/`：二次抽取得到的 Word 文本和 OCR JSON 副本。

为保持现有数据库路径和重新构建流程可用，这里采用复制而不是移动；`resource/` 下原件未修改。
北交大月考文件不在本目录中。
"""
    (STAGE / "说明.md").write_text(readme, encoding="utf-8")

    result = {
        "staged_sources": len(manifest_rows),
        "legacy_docs": category_counts["旧版DOC（首轮无文本）"],
        "scanned_pdfs": category_counts["扫描PDF（首轮无可用文本）"],
        "missing_sources": missing,
        "copied_bytes": copied_bytes,
        "residual_issue_rows": len(issue_rows),
        "residual_issue_sources": len({row["源文件"] for row in issue_rows}),
        "stage": str(STAGE),
    }
    (STAGE / "整理结果.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
