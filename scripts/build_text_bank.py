"""Build the optional text bank on top of the existing image manifests.

The image files remain the source of truth for originals and are still imported.
This script joins every question/answer image with its MinerU Markdown sibling,
rewrites inline figures to authenticated media URLs, and emits one additive
manifest whose only new problem fields are ``question_text`` and
``answer.text``. Existing manifests are never modified.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

from PIL import Image

ROOT = Path(__file__).resolve().parents[1]
TEXT_ROOT = "devided-math-text"
SOURCE_MANIFESTS = (
    "data/nonmath-manifest.json",
    "data/kaoyan/math1-manifest.json",
    "data/kaoyan/math2-manifest.json",
    "data/kaoyan/math3-manifest.json",
    "data/bjtu-finals/calculus1-manifest.json",
    "data/bjtu-finals/calculus2-manifest.json",
    "data/bjtu-finals/linear-algebra-manifest.json",
    "data/bjtu-finals/probability-manifest.json",
)
MAX_TEXT = 50000

_ARTIFACT_LINE = re.compile(
    r"^(?:评阅人|阅卷人|装订线|密封线|第\s*[0-9一二三四五六七八九十百]+\s*页)\s*$"
)
_ARTIFACT_PREFIX = re.compile(r"^(?:评阅人|阅卷人)\s*")
_SCORE_PREFIX = re.compile(r"^得分\s*(?=[0-9一二三四五六七八九十（(])")
_SCORE_CELL_ROW = re.compile(r"^\|\s*(?:得分|评阅人)\s*\|\s*\|$")
_LONE_NUMBER = re.compile(r"^\s*[0-9]+\s*[.、]\s*$")
_TRAILING_REVIEWER = re.compile(r"\s*(?:评阅人|阅卷人)\s*$")
_TAG = re.compile(r"\s*\\tag\s*\{[^{}]*\}")
_NESTED_TEXT_DOLLAR = re.compile(
    r"(\\(?:mathrm|text|operatorname)\s*\{)\s*\$([^$]*)\$\s*(\})"
)
_LEADING_ORDER = re.compile(r"(?m)^([ \t]*)([0-9]+)\.(?=\s)")
_NUMERIC_PAIR = re.compile(r"^(\d+)\s+(\d+)$")
_DIV_TAG = re.compile(r"</?div[^>]*>")

# A few scanned pages cannot be recovered mechanically. These are deliberately
# explicit, reviewable corrections with no image references.
MANUAL_TEXT: dict[str, dict[str, str]] = {
    "kaoyan-math1-2011-22": {
        "question": """(22)(本题满分11分)

设随机变量 $X$ 与 $Y$ 的概率分布分别为：

| $X$ | $0$ | $1$ |
| --- | --- | --- |
| $P$ | $\\frac{1}{3}$ | $\\frac{2}{3}$ |

| $Y$ | $-1$ | $0$ | $1$ |
| --- | --- | --- | --- |
| $P$ | $\\frac{1}{3}$ | $\\frac{1}{3}$ | $\\frac{1}{3}$ |

且 $P\\{X^{2}=Y^{2}\\}=1$。

(I) 求二维随机变量 $(X,Y)$ 的概率分布；
(II) 求 $Z=XY$ 的概率分布；
(III) 求 $X$ 与 $Y$ 的相关系数 $\\rho_{XY}$。""",
        "answer": """【解析】由 $P\\{X^{2}=Y^{2}\\}=1$，得 $P\\{X^{2}\\ne Y^{2}\\}=0$，故
$P\\{X=0,Y=-1\\}=P\\{X=0,Y=1\\}=P\\{X=1,Y=0\\}=0$。联合分布为：

| $X\\backslash Y$ | $-1$ | $0$ | $1$ |
| --- | --- | --- | --- |
| $0$ | $0$ | $\\frac{1}{3}$ | $0$ |
| $1$ | $\\frac{1}{3}$ | $0$ | $\\frac{1}{3}$ |

（II）$Z=XY$ 的分布为：

| $Z$ | $-1$ | $0$ | $1$ |
| --- | --- | --- | --- |
| $P$ | $\\frac{1}{3}$ | $\\frac{1}{3}$ | $\\frac{1}{3}$ |

（III）$E(XY)=0$，$E(X)=\\frac{2}{3}$，$E(Y)=0$，故
$\\rho_{XY}=\\frac{E(XY)-E(X)E(Y)}{\\sqrt{D(X)D(Y)}}=0$。"""
    },
    "kaoyan-math2-2014-13": {
        "question": """(13) 一根长为 1 的细棒位于 $x$ 轴的区间 $[0,1)$ 上，若其线密度 $\\rho(x) = -x^{2} + 2x + 1$，则该细棒的质心横坐标 $\\bar{x} = \\underline{\\quad}$。"""
    },
    "kaoyan-math2-2016-04": {
        "question": """(4) 设函数 $f(x)$ 在 $(-\\infty,+\\infty)$ 内连续，其导函数 $y=f'(x)$ 的图形具有如下特征：在竖直虚线左侧，$f'(x)$ 从正值下降并穿过 $x$ 轴；在虚线处无定义；在虚线右侧，$f'(x)$ 先增后减再增，并在 $x$ 轴处与 $x$ 轴相切（局部最小值为 $0$）。则（ ）

(A) 函数 $f(x)$ 有 2 个极值点，曲线 $y=f(x)$ 有 2 个拐点
(B) 函数 $f(x)$ 有 2 个极值点，曲线 $y=f(x)$ 有 3 个拐点
(C) 函数 $f(x)$ 有 3 个极值点，曲线 $y=f(x)$ 有 1 个拐点
(D) 函数 $f(x)$ 有 3 个极值点，曲线 $y=f(x)$ 有 2 个拐点"""
    },
    "bjtu-final-linear-algebra-2022-s02-q15": {"answer": "B"},
    "bjtu-final-linear-algebra-2024-s01-q07": {"answer": "D"},
}


def _fix_vertical_fraction(
    text: str, image_size: tuple[int, int] | None
) -> str:
    """Recover a stacked fraction that OCR flattened to two numbers.

    ``16`` over ``15`` was exported as ``16 15``; a narrow, tall source image
    is the signal we use to avoid changing ordinary two-number answers.
    """
    match = _NUMERIC_PAIR.fullmatch(text.strip())
    if not match or not image_size or not all(image_size):
        return text
    width, height = image_size
    if not width or height < width * 1.6:
        return text
    return f"$\\frac{{{match.group(1)}}}{{{match.group(2)}}}$"


def _normalize_ambiguous_dollars(text: str) -> str:
    """Turn table/row ``$$`` separators into two inline math segments.

    A line that is a lone ``$$`` fence or a complete same-line display is left
    untouched. This keeps MinerU table headers such as ``${Z}_{2}$${Z}_{1}$``
    renderable instead of discarding the whole answer.
    """
    lines = []
    for line in text.splitlines():
        stripped = line.strip()
        if (
            "$$" in stripped
            and stripped != "$$"
            and not (
                stripped.startswith("$$")
                and stripped.endswith("$$")
                and stripped.count("$$") == 2
            )
        ):
            line = line.replace("$$", "$ $")
        lines.append(line)
    return "\n".join(lines)


def clean_text(text: str) -> str:
    """Remove scan furniture without changing the mathematics."""
    text = text.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in text.split("\n"):
        line = line.rstrip()
        stripped = line.strip()
        if (
            not stripped
            or _ARTIFACT_LINE.match(stripped)
            or _SCORE_CELL_ROW.match(stripped)
            or _LONE_NUMBER.match(stripped)
        ):
            continue
        line = _ARTIFACT_PREFIX.sub("", line)
        line = _SCORE_PREFIX.sub("", line)
        line = _TRAILING_REVIEWER.sub("", line)
        if line.strip():
            lines.append(line)
    text = "\n".join(lines)
    # Score tags (including malformed ones with inline dollars) are not part of
    # the mathematics and would break KaTeX when they appear in inline math.
    # Keep a math-safe spacing command so we do not create an empty ``$$`` pair.
    text = _TAG.sub(lambda _: " \\quad ", text)
    # MinerU sometimes puts a second pair of dollars around Chinese text inside
    # \mathrm{...}/\text{...}; that split the outer formula into invalid pieces.
    text = _NESTED_TEXT_DOLLAR.sub(
        lambda m: m.group(1) + m.group(2) + m.group(3), text
    )
    text = _DIV_TAG.sub("", text)
    text = _normalize_ambiguous_dollars(text)
    # Keep visible question/step numbers but stop Markdown from turning a
    # question starting with "1." into an ordered list (and indenting the stems).
    text = _LEADING_ORDER.sub(r"\1\2\\.", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def _image_destination(raw: str) -> str:
    raw = raw.strip()
    if raw.startswith("<") and ">" in raw:
        return raw[1 : raw.index(">")].strip()
    # MinerU keeps literal spaces in a few filenames (``1 (4).assets``), so the
    # whole destination is used instead of splitting on whitespace.
    return raw


def iter_image_refs(text: str):
    """Yield (start, end, destination) for Markdown image links.

    Parentheses inside filenames are common here (``1 (4).assets/...``), so a
    plain regex would truncate the path.
    """
    index = 0
    while True:
        start = text.find("![", index)
        if start < 0:
            return
        close = text.find("]", start + 2)
        if close < 0 or close + 1 >= len(text) or text[close + 1] != "(":
            index = start + 2
            continue
        cursor = close + 2
        depth = 1
        while cursor < len(text):
            char = text[cursor]
            if char == "\\":
                cursor += 2
                continue
            if char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    break
            cursor += 1
        if depth:
            return
        yield start, cursor, _image_destination(text[close + 2 : cursor])
        index = cursor + 1


def _relative_source(path: Path, root: Path) -> str:
    return path.resolve().relative_to(root.resolve()).as_posix()


def _inline_asset(problem_id: str, purpose: str, source: Path, root: Path) -> dict:
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    suffix = source.suffix.lower() or ".png"
    asset_id = (
        "img-"
        + digest[:18]
        + hashlib.sha256((problem_id + "|" + purpose).encode()).hexdigest()[:4]
        + ("q" if purpose == "question" else "a")
    )
    content_type = mimetypes.guess_type(source.name)[0] or "image/png"
    try:
        with Image.open(source) as image:
            width, height = image.size
    except OSError as exc:  # pragma: no cover - defensive for damaged scans
        raise ValueError(f"Unreadable inline image: {source}") from exc
    return {
        "id": asset_id,
        "owner_id": None,
        "purpose": purpose,
        "path": digest + suffix,
        "content_type": content_type,
        "width": width,
        "height": height,
        "problem_id": problem_id,
        "source_path": _relative_source(source, root),
    }


def render_part(
    markdown_path: Path,
    problem_id: str,
    purpose: str,
    root: Path,
    image_size: tuple[int, int] | None = None,
) -> tuple[str, list[dict], str | None]:
    """Rewrite inline figures and return clean Markdown plus new assets."""
    source = markdown_path.read_text(encoding="utf-8")
    text = clean_text(source)
    if not text:
        return "", [], "empty text file"
    assets: list[dict] = []
    replacements: list[tuple[int, int, str]] = []
    for start, end, destination in iter_image_refs(text):
        if not destination:
            continue
        if destination.startswith(("http://", "https://", "data:", "/", "#")):
            continue
        local = unquote(destination.split("#", 1)[0])
        image_path = (markdown_path.parent / local).resolve()
        if not image_path.is_file():
            replacements.append((start, end, "*（原图略）*"))
            continue
        if not image_path.is_relative_to((root / TEXT_ROOT).resolve()):
            replacements.append((start, end, "*（原图略）*"))
            continue
        asset = _inline_asset(problem_id, purpose, image_path, root)
        if all(existing["id"] != asset["id"] for existing in assets):
            assets.append(asset)
        alt_end = text.find("]", start)
        alt = text[start + 2 : alt_end]
        replacements.append(
            (start, end, f"![{alt}](/api/v1/media/{asset['id']})")
        )
    for start, end, new_text in reversed(replacements):
        text = text[:start] + new_text + text[end + 1 :]
    return _fix_vertical_fraction(text, image_size), assets, None


def _join(parts: list[str]) -> str:
    return "\n\n".join(part.strip() for part in parts if part.strip()).strip()


def build_text_bank(root: Path = ROOT) -> tuple[dict, dict]:
    sources: dict[str, dict] = {}
    assets: dict[str, dict] = {}
    problems: list[dict] = []
    fallbacks: list[dict] = []
    inline_assets: dict[str, dict] = {}
    max_question = 0
    max_answer = 0
    for relative in SOURCE_MANIFESTS:
        path = root / relative
        if not path.is_file():
            raise FileNotFoundError(path)
        manifest = json.loads(path.read_text(encoding="utf-8"))
        for source in manifest["sources"]:
            previous = sources.get(source["id"])
            if previous and previous != source:
                raise ValueError("Conflicting source: " + source["id"])
            sources[source["id"]] = source
        for asset in manifest["assets"]:
            previous = assets.get(asset["id"])
            if previous and previous != asset:
                raise ValueError("Conflicting asset: " + asset["id"])
            assets[asset["id"]] = asset
        manifest_assets = {asset["id"]: asset for asset in manifest["assets"]}
        for problem in manifest["problems"]:
            problem = json.loads(json.dumps(problem, ensure_ascii=False))
            question_parts: list[str] = []
            answer_parts: list[str] = []
            question_inline: list[dict] = []
            answer_inline: list[dict] = []
            question_reason: str | None = None
            answer_reason: str | None = None
            for purpose, ids, parts, inline_target in (
                ("question", problem["question_images"], question_parts, question_inline),
                ("answer", problem["answer"]["images"], answer_parts, answer_inline),
            ):
                for asset_id in ids:
                    source_asset = manifest_assets[asset_id]
                    # Build the sibling path without relying on the image suffix.
                    markdown = (
                        root
                        / TEXT_ROOT
                        / (
                            source_asset["source_path"][len("devided-math/") :].rsplit(
                                ".", 1
                            )[0]
                            + ".md"
                        )
                    )
                    if not markdown.is_file():
                        # A missing/blank fragment is usually a page break or a
                        # split scan. Ignore it if other fragments carry text.
                        parts.append("")
                        continue
                    text, inline, error = render_part(
                        markdown,
                        problem["id"],
                        purpose,
                        root,
                        (
                            source_asset.get("width"),
                            source_asset.get("height"),
                        ),
                    )
                    if error:
                        parts.append("")
                        continue
                    inline_target.extend(inline)
                    parts.append(text)
            question_text = _join(question_parts)
            answer_text = _join(answer_parts)
            manual = MANUAL_TEXT.get(problem["id"], {})
            if "question" in manual:
                question_text = manual["question"].strip()
                question_inline = []
            if "answer" in manual:
                answer_text = manual["answer"].strip()
                answer_inline = []
            if not question_text:
                question_reason = "empty question text"
            if not answer_text:
                answer_reason = "empty answer text"
            if question_reason:
                fallbacks.append(
                    {
                        "id": problem["id"],
                        "part": "question",
                        "reason": question_reason,
                    }
                )
                question_text = ""
            else:
                for inline_asset in question_inline:
                    previous = inline_assets.get(inline_asset["id"])
                    if previous and previous != inline_asset:
                        raise ValueError(
                            "Conflicting inline asset: " + inline_asset["id"]
                        )
                    inline_assets[inline_asset["id"]] = inline_asset
            if answer_reason:
                fallbacks.append(
                    {
                        "id": problem["id"],
                        "part": "answer",
                        "reason": answer_reason,
                    }
                )
                answer_text = ""
            else:
                for inline_asset in answer_inline:
                    previous = inline_assets.get(inline_asset["id"])
                    if previous and previous != inline_asset:
                        raise ValueError(
                            "Conflicting inline asset: " + inline_asset["id"]
                        )
                    inline_assets[inline_asset["id"]] = inline_asset
            problem["question_text"] = question_text
            problem["answer"]["text"] = answer_text
            max_question = max(max_question, len(question_text))
            max_answer = max(max_answer, len(answer_text))
            if len(question_text) > MAX_TEXT or len(answer_text) > MAX_TEXT:
                raise ValueError("Text exceeds limit: " + problem["id"])
            problems.append(problem)
    ids = [problem["id"] for problem in problems]
    if len(ids) != len(set(ids)):
        raise ValueError("Duplicate problem IDs across manifests")
    for asset in inline_assets.values():
        if asset["id"] in assets:
            raise ValueError("Inline asset id collides: " + asset["id"])
        assets[asset["id"]] = asset
    manifest = {
        "schema_version": 1,
        "sources": [sources[key] for key in sorted(sources)],
        "assets": [assets[key] for key in sorted(assets)],
        "problems": problems,
    }
    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "problems": len(problems),
        "text_ready": sum(
            1
            for problem in problems
            if problem["question_text"] and problem["answer"]["text"]
        ),
        "image_fallback": fallbacks,
        "image_fallback_problems": len({item["id"] for item in fallbacks}),
        "inline_assets": len(inline_assets),
        "assets": len(assets),
        "sources": len(sources),
        "max_question_text": max_question,
        "max_answer_text": max_answer,
    }
    return manifest, report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--output", type=Path, default=ROOT / "data/text-bank-manifest.json"
    )
    parser.add_argument(
        "--report", type=Path, default=ROOT / "data/text-bank-report.json"
    )
    parser.add_argument("--check", action="store_true", help="Validate only")
    parser.add_argument("--replace", action="store_true", help="Overwrite output")
    args = parser.parse_args()
    manifest, report = build_text_bank()
    print(
        json.dumps(
            {
                "problems": report["problems"],
                "text_ready": report["text_ready"],
                "image_fallback": report["image_fallback_problems"],
                "inline_assets": report["inline_assets"],
                "max_question_text": report["max_question_text"],
                "max_answer_text": report["max_answer_text"],
            },
            ensure_ascii=False,
        )
    )
    for item in report["image_fallback"]:
        print("fallback:", item["id"], item["reason"])
    if args.check:
        return 0
    if args.output.exists() and not args.replace:
        print(
            f"{args.output} exists; pass --replace to regenerate", file=sys.stderr
        )
        return 1
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print("wrote", args.output, "and", args.report)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
