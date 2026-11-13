"""线上发布包：升级脚本必须与迁移同步，检查脚本与手册覆盖关键步骤。

升级 SQL 是生成文件，权威来源仍是 supabase/migrations/ 下按顺序执行的迁移；
这里守住两条底线：生成结果没有漂移、整段脚本仍然只有一个事务。
"""

import importlib.util
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RELEASE = ROOT / "supabase" / "release"
UPGRADE = RELEASE / "20261113_upgrade.sql"
PREFLIGHT = RELEASE / "20261113_preflight.sql"
POSTCHECK = RELEASE / "20261113_postcheck.sql"
MANUAL = ROOT / "docs" / "release-2026-11-13-ops.md"

TABLES = (
    "learning_analyses",
    "mock_papers",
    "practice_variants",
    "variant_cache",
    "submission_transcripts",
)
COLUMNS = (
    "knowledge_points",
    "knowledge_source",
    "cache_id",
    "source_hash",
    "reused",
    "transcript",
)


def read(path):
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def generator():
    spec = importlib.util.spec_from_file_location(
        "build_release_sql", ROOT / "scripts" / "build_release_sql.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_upgrade_sql_is_generated_from_current_migrations():
    assert generator().build() == read(UPGRADE)


def test_upgrade_sql_is_one_transaction_with_one_reload():
    text = read(UPGRADE)
    lines = [line.strip() for line in text.splitlines()]
    assert lines.count("begin;") == 1
    assert lines.count("commit;") == 1
    assert lines.index("begin;") < lines.index("commit;")
    assert lines.count("notify pgrst, 'reload schema';") == 1
    assert lines.index("notify pgrst, 'reload schema';") < lines.index("commit;")
    assert text.count("$$") % 2 == 0


def test_upgrade_sql_has_guard_and_ordered_sections():
    text = read(UPGRADE)
    assert "检测到 2026-11-13 发布的对象已存在" in text
    assert text.index("do $$") < text.index("create table public.learning_analyses")
    module = generator()
    positions = []
    for name, _ in module.MIGRATIONS:
        marker = "-- 源文件：supabase/migrations/" + name
        assert text.count(marker) == 1
        positions.append(text.index(marker))
    assert positions == sorted(positions)
    for table in TABLES:
        assert text.count("create table public." + table) == 1


def test_checks_cover_every_new_object():
    pre, post = read(PREFLIGHT), read(POSTCHECK)
    for table in TABLES:
        assert table in pre
        assert table in post
    for column in COLUMNS:
        assert column in pre
        assert column in post
    assert "本发布状态" in pre
    assert "部分执行" in pre
    assert "总体结果" in post
    assert "'PASS'" in post and "'FAIL'" in post
    assert "relrowsecurity" in post
    assert "aclexplode" in post


def test_manual_covers_release_files_and_key_settings():
    manual = read(MANUAL)
    for name in (
        "20261113_preflight.sql",
        "20261113_upgrade.sql",
        "20261113_postcheck.sql",
    ):
        assert name in manual
    assert "AI_TIMEOUT" in manual
    assert "先迁移数据库，再部署代码" in manual
    assert "verify_knowledge.py" in manual
    assert "回滚" in manual
