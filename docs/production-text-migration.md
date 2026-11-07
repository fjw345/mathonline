# 文字版题目上线说明（原图片只保留在数据库）

本文说明如何把 `devided-math-text/` 生成的文字题面、文字答案和 52 张内嵌图迁移到**已经部署的线上 Supabase**，并在不删除、不替换原图片资源的前提下，把公开网页切换为只显示文字版题目。

> 当前迁移脚本已准备好并通过测试；线上库尚未执行任何写入。最后部署由维护者执行。
>
> **推荐发布顺序（避免线上出现“文字版整理中”）：**
> 1. Supabase SQL 迁移；
> 2. 本地 dry-run；
> 3. 本地执行文字回填；
> 4. 本地验证数据库（此时线上旧代码仍显示图片）；
> 5. 部署代码，切换为文字页面；
> 6. 公开页验收。

## 1. 上线后的表现

- 公开页面只显示文字：题目页、题卡、答案面板都不再渲染原题截图。
- 题干使用 `question_text`，答案使用 `problem_answers.text`，内容为 Markdown + LaTeX。
- 文字中的几何图仍通过 `/api/v1/media/<asset-id>` 按题目可见性和答案解锁权限显示。
- 原题/答案截图仍保留在 `question_images`、`images`、`media_assets` 和私有 Storage 中，但公开媒体接口拒绝读取；管理员维护页仍可打开，便于换图、核对和修文字。
- 1,868 道题现在都有文字，没有“回退原图”的题；个别 OCR 坏公式显示“公式待校对”。
- 不需要额外开关：**部署包含文字渲染的代码就是切换动作**。只执行数据库回填、不部署代码时，旧页面仍显示图片，不会报错。

## 2. 数据保留原则

迁移只新增/写入两个文字列和 52 个内嵌图资产，其他数据不动：

| 数据 | 迁移动作 |
| --- | --- |
| `problems.question_images`、顺序 | 不写，保持不变 |
| `problem_answers.images`、顺序 | 不写，保持不变 |
| 原有 4,138 个 `media_assets` 图片记录 | 不删除、不修改 |
| Storage 中原有题图、答案图、用户图片 | 不删除、不覆盖 |
| `problems.version` | 不递增 |
| `problems.updated_at` | 不修改 |
| `problems.manual_revision` | 不修改 |
| 作答、错题、每日分配、答案解锁、点赞、题解、审核记录 | 不访问、不修改 |
| `question_text` / `problem_answers.text` | 新增列并写入文字 |
| 52 张文字内嵌图 | 新增 Storage 对象和 `media_assets` 行 |

原图片资源始终保留在数据库/私有 Storage 中；只是公开网页不再提供查看入口，公开媒体接口对原题截图返回 403。管理员维护页仍可访问。

## 3. 迁移包

| 文件 | 用途 |
| --- | --- |
| [`supabase/migrations/202611060001_problem_text.sql`](../supabase/migrations/202611060001_problem_text.sql) | 加列、更新 `save_problem`、新增批量回填函数 |
| [`scripts/apply_text_bank.py`](../scripts/apply_text_bank.py) | 上传 52 张内嵌图，回填 1,868 道题文字；自动备份；默认 dry-run |
| [`scripts/verify_text_migration.py`](../scripts/verify_text_migration.py) | 只读验证文字数量、图片引用、内嵌资产、Storage 和版本 |
| [`scripts/rollback_text_migration.py`](../scripts/rollback_text_migration.py) | 从迁移前快照恢复两个文字列 |
| [`backend/text_migration.py`](../backend/text_migration.py) | 快照、恢复、验证的公共实现 |
| [`data/text-bank-manifest.json`](../data/text-bank-manifest.json) | 1,868 道题、4,190 个资产（含 52 个内嵌图）的完整文字清单 |
| [`data/text-bank-report.json`](../data/text-bank-report.json) | 生成统计 |

预期规模：

- 题目：1,868
- 题干文字：1,868
- 答案文字：1,868
- 全部题目都有文字：1,868 道（部分答案只有选项字母等短文本）
- 文字内嵌图：52
- 原有图片资产：4,138

## 4. 上线前准备

### 4.1 备份

在 Supabase 控制台确认项目有可用的数据库备份/恢复点。迁移本身是增量操作，但生产写入前仍建议保留恢复点。

### 4.2 配置线上 `.env`

在仓库根目录准备线上环境文件（不要提交、不要发到聊天里）：

```text
APP_MODE=supabase
SUPABASE_URL=https://<ref>.supabase.co
SUPABASE_SERVICE_ROLE_KEY=...
SUPABASE_ANON_KEY=...
SUPABASE_BUCKET=mathonline
```

从 `SUPABASE_URL` 中取出项目 ref，下文用 `<ref>` 表示。

### 4.3 确认清单

```powershell
python -X utf8 scripts/build_text_bank.py --check
```

预期：

```json
{
  "problems": 1868,
  "text_ready": 1868,
  "image_fallback": 0,
  "inline_assets": 52
}
```

### 4.4 不要做什么

- **不要**在线上库执行 `scripts/import_bank.py` 全量导入；那会更新所有题目版本并影响学习记录。
- **不要**为了加文字删除或重建 `problems`、`problem_answers`、`media_assets`。
- **不要**删除 Storage 中原有图片；图片只是不在公开网页展示。
- **不要**手工改 `save_problem` 的旧校验逻辑。

## 5. 第一步：执行数据库结构迁移

在 Supabase Dashboard → SQL Editor 执行：

[`supabase/migrations/202611060001_problem_text.sql`](../supabase/migrations/202611060001_problem_text.sql)

该迁移只做：

1. `alter table public.problems add column if not exists question_text ...`；
2. `alter table public.problem_answers add column if not exists text ...`；
3. `create or replace function public.save_problem(...)`：保留原有校验，增加文字列，并兼容不认识文字字段的旧客户端；
4. 新增 `public.apply_problem_texts(jsonb)`：批量更新文字，不改版本；
5. `notify pgrst, 'reload schema'`。

执行后可以用 SQL 确认列存在：

```sql
select table_name, column_name
from information_schema.columns
where table_schema = 'public'
  and (
    (table_name = 'problems' and column_name = 'question_text')
    or (table_name = 'problem_answers' and column_name = 'text')
  );
```

预期两行。迁移可重复执行，但不要手工改函数体。

## 6. 部署代码并切换文字显示（在回填与验证之后执行）

**先完成第 7–9 步的 dry-run、回填和数据库验证，再执行本节。** 回填期间线上仍是旧版本，继续显示图片；代码部署完成后才切换为文字页面。

部署包含以下改动的后端和前端：

- 公开页面只渲染文字；题卡、题目页、答案面板不再显示原题截图；
- 后端停止为公开题目批量签发原图 URL；公开媒体接口拒绝原题/答案截图；
- 文字中的内嵌图仍按题目可见性和答案解锁权限放行；
- 管理后台仍可查看原图，并可编辑文字字段。

如果你选择先部署代码、后回填文字，公开页会暂时显示“文字版整理中”，不推荐。推荐顺序始终是：**SQL → dry-run → 回填 → 验证数据库 → 部署代码 → 页面验收**。

`vercel.json` 已排除 `devided-math-text/**`，文字源文件不会进入函数包。

## 7. 回填前 dry-run（不写入）

在仓库根目录：

```powershell
python -X utf8 scripts/apply_text_bank.py --confirm-project <ref>
```

预期输出：

```json
{
  "updated": 1868,
  "skipped": 0,
  "missing": [],
  "image_mismatch": 0,
  "assets_added": 0,
  "dry_run": true
}
```

检查：

- `mode` 为 `supabase`
- `project_ref` 等于 `<ref>`
- `missing` 为空
- `dry_run` 为 `true`

dry-run 不写数据库、不占备份文件。

## 8. 执行文字回填

```powershell
python -X utf8 scripts/apply_text_bank.py --apply --confirm-project <ref>
```

脚本会：

1. 再次检查项目 ref、文字列和目标题目；
2. 在 `output/text-migration-backup-<UTC>.json` 写入迁移前文字快照；
3. 上传 52 张内嵌图到 `mathonline` Bucket；
4. 分块调用 `apply_problem_texts` 写入 1,868 道题文字；
5. 输出备份路径和统计。

预期输出：

```json
{
  "updated": 1868,
  "skipped": 0,
  "missing": [],
  "image_mismatch": 0,
  "assets_added": 52,
  "dry_run": false
}
```

说明：

- `updated` 是实际写入的题数；`skipped` 是已有非空文字且未覆盖的题数。
- `image_mismatch` 是图片已被管理员改过、因此跳过文字的题数；这些题在公开页会显示“文字版整理中”，不会显示原图。
- 默认只填空文字；已有管理员文字保留。
- `--overwrite` 只覆盖已有文字，不会忽略图片不匹配。
- `--no-backup` 可跳过备份，但不建议。

## 9. 验证（部署前数据库 + 部署后页面）

### 9.1 部署前数据库验证（只读）

```powershell
python -X utf8 scripts/verify_text_migration.py `
  --backup output/text-migration-backup-<UTC>.json `
  --strict
```

预期：

```json
{
  "ok": true,
  "columns_present": true,
  "expected": {
    "problems": 1868,
    "question_text": 1868,
    "answer_text": 1868,
    "inline_assets": 52
  },
  "missing": [],
  "inline_missing": [],
  "version_changes": [],
  "project_mismatch": false,
  "storage": { "checked": true, "ok": true }
}
```

`version_changes` 为空表示迁移没有改任何题目版本。

如果线上原本就有管理员手工补过的文字，`text_mismatch` 可能非空；去掉 `--strict` 再跑一次，确认差异只是人工修订。不要为了清零而覆盖管理员文字。

### 9.2 部署后的页面验收

至少检查：

- 首页题卡：只显示文字摘要，不再出现题图；
- 题目页：公式正常渲染，选项文字显示，没有“查看题目原图”入口；
- 提交作答或点击“不会”后：答案文字出现，没有“查看答案原图”入口；
- 文字内嵌几何图：按权限加载；
- 公开用户请求原题截图（`/api/v1/media/<原始资产ID>`）：返回 403；
- 管理员登录后：维护页仍可查看原图、编辑文字；
- 手机尺寸：文字、表格、长公式不溢出。

## 10. 回滚

如果文字版有问题，只恢复两个文字列：

```powershell
python -X utf8 scripts/rollback_text_migration.py `
  output/text-migration-backup-<UTC>.json `
  --apply --confirm-project <ref>
```

- 恢复迁移前的 `question_text` / `problem_answers.text`；
- 不恢复版本、`updated_at`、图片；
- 不删除 52 个内嵌图资产（它们会变成未引用资产，无害）；
- 公开页会显示“文字版整理中”，**不会自动重新显示原图**；原图仍保存在 DB/Storage。
- 如果确实要恢复图片展示，需要另外把前端和媒体权限改回旧版本，不是回滚脚本的职责。

## 11. 常见问题

**提示缺少 question_text/problem_answers.text**
先执行第 5 步 SQL；脚本不会代替 DDL。

**提示项目不匹配**
`.env` 的 `SUPABASE_URL` 与 `--confirm-project` 不一致。脚本不会写入。

**提示备份文件已存在**
默认文件名带 UTC 时间；同一秒重复执行时指定新的 `--backup` 路径。

**Storage 上传失败**
检查 Bucket `mathonline`、service role 的 Storage 权限、Bucket 的 `image/png` 限制。上传发生在文字更新之前；失败时不会写文字。

**verify 的 `version_changes` 非空**
说明迁移窗口内题目被其他操作改过。停止并确认来源，不要直接回滚覆盖。

**verify 的 `text_mismatch` 非空**
通常是管理员手工修订；确认内容后保留即可。`--strict` 只是把差异当失败，非严格模式会报告但不阻止。

**`image_mismatch` 非空**
这些题目的图片已与清单不同；脚本已跳过，公开页显示“文字版整理中”。不要为了覆盖而强制写文字。

**公开页仍能拿到原图 URL**
检查是否部署了新后端；新后端不再为公开题目批量签发原图 URL，且 `/api/v1/media` 对原题资产返回 403。旧页面缓存的签名 URL 最长 300 秒后失效。

## 12. 上线验收清单

- [ ] Supabase 备份/恢复点已确认
- [ ] SQL 迁移执行成功，两个文字列存在
- [ ] 代码已部署
- [ ] dry-run 显示 1,868 题、无 missing
- [ ] `--apply` 完成，备份文件已生成
- [ ] `verify_text_migration.py --strict` 通过
- [ ] 首页、题目、答案、内嵌图、手机布局抽查通过
- [ ] 公开用户无法访问原题截图（403）
- [ ] 管理员维护页仍可看原图、改文字
- [ ] 回滚备份路径已记录

完成后公开网页只显示文字版题目；原图片资源仍安全保留在数据库和私有 Storage 中。
