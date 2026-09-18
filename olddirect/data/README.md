# Problem bank build output

Generated artifacts:

- `problem_bank.sqlite3`: local review database.
- `problems.json`: portable import data with source, answer and tag fields.
- `import_report.json`: per-file inclusion and extraction report.

All extracted problems are intentionally stored as `draft` with
`requires_review = 1`. Formula text, options, answers, explanations, source
rights and heuristic difficulty must be checked before changing a problem to
`published`.

BJTU monthly exams are excluded at the source scan boundary. Excluded sources
remain in `problem_sources` with `included = 0` and an exclusion reason, but no
problem row can reference them.

Rebuild and verify:

```powershell
python scripts/build_problem_bank.py
python scripts/verify_problem_bank.py
```

Legacy `.doc` files need Word text sidecars. On an interactive Windows session
with Microsoft Word available, run this before rebuilding:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File scripts/extract_word.ps1
```

Image-only PDF pages are reported with zero text and are not converted into
invented question text. The OCR and deduplication passes are:

```powershell
python scripts/extract_scanned_pdfs.py
python scripts/extract_legacy_docs.py
python scripts/build_problem_bank.py
python scripts/deduplicate_problem_bank.py
python scripts/link_answer_sources.py
python scripts/verify_problem_bank.py
```

Deduplicated questions keep every origin in `problem_source_links` and merge all
tags. Removed duplicate IDs, original text, original answer fields, match type
and similarity are retained in `problem_aliases`. Borderline matches are not
merged; they are written to `dedup_review_candidates` for manual review.
