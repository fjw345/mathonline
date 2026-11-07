# 文字版题面与答案

本页记录 2026-11-06 加入的“文字版题目”渲染层。它建立在既有图片题库之上，**不替换、不删除**原始图片，但公开网页只展示文字版题目，不再提供原题截图查看。

> 将文字数据写入线上 Supabase 的完整步骤、备份、验证和回滚脚本见
> [线上迁移操作手册](production-text-migration.md)。

## 现状

- 题库的原始截图仍保存在 `problems.question_images`、`problem_answers.images`、`media_assets` 和私有 Storage 中，供后台维护和追溯，不对外展示。
- 新增两个字段：`problems.question_text`、`problem_answers.text`，内容为 Markdown + LaTeX（`$...$`、`$$...$$`）。
- 公开页面只渲染文字：题目页、题卡、答案面板都不再显示原题截图；文字中的内嵌几何图仍按权限显示。
- 当前 1,868 道题的题干和答案都有文字，没有图片回退题。
- 原图在公开媒体接口中不可读取；管理员维护页仍可访问原图，便于换图、核对和修文字。

数据来源：`devided-math-text/`（MinerU 从 `devided-math/` 原图转出的 Markdown，目录结构与原图一一对应）。

## 数据结构

| 位置 | 作用 |
| --- | --- |
| `problems.question_text` | 题面 Markdown/LaTeX；为空时公开页显示“文字版整理中”，不显示原图 |
| `problem_answers.text` | 参考答案 Markdown/LaTeX；为空时显示“暂无文字答案” |
| `media_assets`（`purpose` 为 `question`/`answer`，`problem_id` 非空，`source_path` 在 `devided-math-text/`） | 文字中内嵌的几何图；仍放在私有 Storage |
| `media_assets` 中原题/答案截图 | 保留在 DB/Storage；公开媒体接口拒绝读取，仅管理员维护页可用 |
| `data/text-bank-manifest.json` | 由图片清单 + 文字文件生成的完整可导入清单 |
| `data/text-bank-report.json` | 生成统计（当前无回退题） |

文字中的插图被改写为 `/api/v1/media/<asset-id>`，媒体接口仅对文字内嵌图按题目可见性（答案还需解锁）放行；原题截图的 `purpose` 也是 `question`/`answer`，但 `source_path` 在 `devided-math/` 或上传目录，因此公开请求返回 403。

## 本地生成与检查

```powershell
# 只校验，不写文件
python -X utf8 scripts/build_text_bank.py --check

# 重新生成（会覆盖生成文件，不动任何原清单）
python -X utf8 scripts/build_text_bank.py --replace
```

生成器不会修改 `data/nonmath-manifest.json`、`data/kaoyan/*-manifest.json`、`data/bjtu-finals/*-manifest.json`。

## 全新本地库

```powershell
$env:APP_MODE = 'local'
$env:LOCAL_DATABASE = 'tmp/text-import.sqlite'
python -X utf8 scripts/import_bank.py data/text-bank-manifest.json
```

新库导入会按清单创建题目并写入文字，版本从 1 开始。使用 `启动数学刷题平台.cmd` 时，启动器会在导入首批非数题后自动调用文字回填；只填空字段，不改版本。

## 线上已有库（非破坏式）

线上库已有 1,868 道题、作答记录、答案解锁和图片；**不要为了加文字重新全量导入**，否则会递增所有题目版本，打乱学习记录。按以下顺序（执行前先确认 Supabase 项目有可用备份）：

1. 在 Supabase SQL Editor 执行
   [`supabase/migrations/202611060001_problem_text.sql`](../supabase/migrations/202611060001_problem_text.sql)。
   该迁移只做 `add column if not exists`、新增/替换函数；不删除表、列或行。
2. 部署包含本功能的后端与前端。
3. 在仓库根目录，用线上服务端环境变量先试运行：

   ```powershell
   $env:APP_MODE = 'supabase'
   python -X utf8 scripts/apply_text_bank.py
   ```

   默认是 dry-run，只打印将更新多少题，不写入。
4. 确认目标库和数量后写入：

   ```powershell
   python -X utf8 scripts/apply_text_bank.py --apply
   ```

   该命令只更新 `question_text` 和 `problem_answers.text`；**不修改** `version`、`updated_at`、`manual_revision`、图片、作答、答案权限或每日分配。
   默认只填空字段，已有非空文字（例如管理员修订）会被保留。若题目的图片引用与清单不同（管理员换过图），该题会被跳过并显示“文字版整理中”，避免把旧扫描文字配到新图；`--overwrite` 只覆盖已有文字，不会忽略图片不匹配。确需强制覆盖文字时显式加 `--overwrite`。
5. 抽题验证：题干显示公式、答案需提交后可见、文字内嵌插图可加载；原题截图不再出现在公开页面。

迁移未执行时，脚本会检测到缺少文字列并拒绝写入，不会产生半成品数据。

## 回滚

- 代码回滚后，旧前端会忽略新增字段；新增列仍可保留，不需要删列。
- 如需停止显示文字，可把相关题目的 `question_text` / `problem_answers.text` 置空；公开页会显示“文字版整理中”，**不会重新显示原图**。不要 `drop column`，以免影响正在运行的新旧版本混部。
- 原图仍保存在 DB/Storage，只有管理员维护接口可以访问；恢复显示图片需要另外改回前端和媒体权限，不是这次回滚脚本的默认行为。

## 已知质量边界

- 文字来自自动识别，个别公式、分数或题号可能识别错误；页面明确提示“自动识别生成”，用户可提交反馈，但不能在公开页查看原图。
- 1,868 道题现在都有文字；少数 OCR 坏公式由 KaTeX 显示“公式待校对”，不展示原始 TeX。
- 管理员维护页仍可查看原图，用于核对文字、换图和修图；公开用户不能通过 API、签名 URL 或 Storage 直链读取原题截图。
- 关键词搜索现在包含文字题面；图片内容不作为公开检索来源。
