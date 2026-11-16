"""把 2026-11-13 的 6 份迁移合并成一份可整段执行的线上发布 SQL。

迁移文件仍是权威来源：每份各自一个事务，可以单独粘贴执行，适合“已经做了一部分”
的补救场景。本脚本只做机械合并，规则固定、不做语义改写：

1. 去掉每份文件自己的 ``begin;`` / ``commit;``，整份发布只保留一对；
2. 去掉重复的 ``notify pgrst``，在末尾统一发一次；
3. 在开头加一段保护：检测到本发布的对象已存在时报错退出，避免重复执行。

用法（改过任何一份迁移后都要重新生成，发布前用 --check 核对）：

    python -X utf8 scripts/build_release_sql.py
    python -X utf8 scripts/build_release_sql.py --check
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "supabase" / "release" / "20261113_upgrade.sql"

MIGRATIONS = [
    ("202611130001_knowledge_points.sql", "知识点列、索引、save_problem 与批量标注函数"),
    ("202611130002_learning_analysis.sql", "学情分析报告缓存表"),
    ("202611130003_mock_exams.sql", "考试预测模拟卷表"),
    ("202611130004_practice_variants.sql", "一题多练变式题表"),
    ("20261113120000_shared_variants.sql", "AI 仿题共享题池与变式题新增列"),
    ("20261113130000_image_transcript.sql", "图片作答转写表与判分记录新增列"),
    ("20261113140000_user_ai_keys.sql", "用户自带 API 密钥的加密存储表"),
]

NOTIFY = "notify pgrst, 'reload schema';"

HEADER = """\
-- ============================================================================
-- 数学刷题平台：2026-11-13 线上数据库升级
-- 知识点标签 / AI 学情分析 / 考试预测模拟卷 / 错题 AI 变式 / 共享题池 / 图片作答转写 / 用户密钥
--
-- 目标：Supabase PostgreSQL（public schema）
--
-- 用法：
--   1. 先执行 20261113_preflight.sql，确认状态为「未执行」、前置条件全部为 true。
--   2. 在 SQL Editor 中整段粘贴执行本文件，只需执行一次。
--   3. 再执行 20261113_postcheck.sql，确认总体结果为 PASS。
--
-- 本文件是一个事务：全部语句成功才提交，任何一步失败整段回滚，不会留下半套结构。
-- 请勿分段执行，请勿重复执行（开头的保护段会检测并报错）。
-- 前置迁移：20261110000000_ai_grading.sql（problem_grading 表）必须已执行。
--
-- 本文件由 scripts/build_release_sql.py 从以下迁移机械合并生成，请勿手工编辑：
--   supabase/migrations/202611130001_knowledge_points.sql
--   supabase/migrations/202611130002_learning_analysis.sql
--   supabase/migrations/202611130003_mock_exams.sql
--   supabase/migrations/202611130004_practice_variants.sql
--   supabase/migrations/20261113120000_shared_variants.sql
--   supabase/migrations/20261113130000_image_transcript.sql
--   supabase/migrations/20261113140000_user_ai_keys.sql
-- 重新生成：python -X utf8 scripts/build_release_sql.py
-- ============================================================================
"""

GUARD = """\
-- 保护：本发布只要有任何对象已存在，就停止执行，避免“重复建表”报错扰乱判断。
-- 若确实只完成了部分迁移，请按 preflight 的提示逐个执行缺失的迁移文件。
do $$
begin
  if to_regclass('public.learning_analyses') is not null
     or to_regclass('public.mock_papers') is not null
     or to_regclass('public.practice_variants') is not null
     or to_regclass('public.variant_cache') is not null
     or to_regclass('public.submission_transcripts') is not null
     or to_regclass('public.user_ai_keys') is not null
     or exists (
          select 1
          from information_schema.columns
          where table_schema = 'public'
            and table_name = 'problems'
            and column_name = 'knowledge_points'
        )
  then
    raise exception using
      message = '检测到 2026-11-13 发布的对象已存在，已停止执行，数据库未改动。',
      hint = '若上次只完成了部分迁移，请先执行 supabase/release/20261113_preflight.sql 查看缺失段，再逐个执行 supabase/migrations/ 中缺失的文件；不要重复执行本脚本。';
  end if;
end
$$;
"""

FOOTER = """\
-- 全部结构创建完成；NOTIFY 会在事务提交后送达 PostgREST，使新表立即可用。
"""


def _strip_transaction(name, text):
    lines = text.splitlines()
    kept, begins, commits = [], 0, 0
    for line in lines:
        stripped = line.strip().lower()
        if stripped == "begin;":
            begins += 1
            continue
        if stripped == "commit;":
            commits += 1
            continue
        kept.append(line)
    if begins != 1 or commits != 1:
        raise SystemExit(
            "%s: 期望恰好一个 begin; 和一个 commit;，实际 %d / %d" % (name, begins, commits)
        )
    body = "\n".join(kept).strip("\n")
    # 每段末尾都会重复发送 notify；合并到文件末尾统一发一次。
    body = "\n".join(
        line for line in body.splitlines() if line.strip().lower() != NOTIFY
    ).strip("\n")
    return body


def build():
    sections = []
    for index, (name, title) in enumerate(MIGRATIONS, 1):
        path = ROOT / "supabase" / "migrations" / name
        body = _strip_transaction(name, path.read_text(encoding="utf-8"))
        sections.append(
            "-- " + "=" * 74 + "\n"
            "-- 第 %d/%d 段：%s\n"
            "-- 源文件：supabase/migrations/%s\n"
            % (index, len(MIGRATIONS), title, name)
            + "-- " + "=" * 74 + "\n"
            + body
        )

    parts = [HEADER.rstrip("\n"), "", "begin;", "", GUARD.rstrip("\n"), ""]
    parts.append("\n\n".join(sections))
    parts.extend(
        ["", FOOTER.rstrip("\n"), "", "notify pgrst, 'reload schema';", "", "commit;", ""]
    )
    return "\n".join(parts)


def main():
    parser = argparse.ArgumentParser(description="生成 20261113 线上发布 SQL")
    parser.add_argument(
        "--check",
        action="store_true",
        help="只核对已提交的发布文件是否与迁移同步，不写文件",
    )
    args = parser.parse_args()

    content = build()
    if args.check:
        if not OUTPUT.exists():
            print("缺少 %s，请先运行本脚本生成。" % OUTPUT.relative_to(ROOT), file=sys.stderr)
            return 1
        current = OUTPUT.read_text(encoding="utf-8").replace("\r\n", "\n")
        if current != content:
            print(
                "发布 SQL 与迁移不一致，请重新运行：python -X utf8 scripts/build_release_sql.py",
                file=sys.stderr,
            )
            return 1
        print("发布 SQL 与迁移一致：%s" % OUTPUT.relative_to(ROOT))
        return 0

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(content, encoding="utf-8", newline="\n")
    print("已生成 %s（%d 行）" % (OUTPUT.relative_to(ROOT), content.count("\n")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
