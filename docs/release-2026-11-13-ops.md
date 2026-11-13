# 2026-11-13 线上发布手册（部署人员版）

本手册给执行线上发布的人使用：按顺序做完第 2–6 节即可发布，第 7 节是回滚，第 9 节用于签收。

| 项目 | 内容 |
| --- | --- |
| 线上站点 | https://www.mathonline.top/ |
| 发布内容 | 知识点标签与筛选、AI 学情分析、北交考试预测/模拟卷、错题 AI 变式、AI 仿题共享题池、图片作答转写、用户自带 DeepSeek 密钥 |
| 结构变更 | 新增 6 张表、5 个索引、11 个列、1 个函数；重定义 `save_problem` 以保护知识点 |
| 数据影响 | 只改 `problems` 的 `knowledge_points`、`knowledge_source`、`difficulty` 三列（标注步骤）；题面、答案、图片、题目版本、学习记录都不动。新增的 `user_ai_keys` 只保存用户密钥的 AES-GCM 密文与末四位掩码 |
| 预计耗时 | 结构升级约 2 分钟；代码部署约 10 分钟；知识点标注 20–40 分钟；冒烟 20 分钟 |
| 建议窗口 | 低峰 30–60 分钟；标注可在发布后单独执行，但完成前知识点筛选为空、考试预测不可用 |
| 关联文档 | 功能、接口、风险说明见 [知识点、AI 学情、考试预测与一题多练](knowledge-analysis-mock-deployment.md) |

## 0. 角色与前置检查

| 角色 | 负责 | 需要的权限 |
| --- | --- | --- |
| 维护者 | 合并推送代码、本机执行标注脚本与只读校验 | GitHub 仓库、本机 Supabase/AI 密钥 |
| 部署人员 | 执行数据库脚本、配置 Vercel 环境变量、触发部署 | Supabase 项目、Vercel 项目 |
| 验收人员 | 用普通账号走第 6 节冒烟清单 | 线上测试账号 |

发布前逐项确认：

- [ ] 打开的是**线上** Supabase 项目（Settings → General 的 Project ID 与线上一致）。
- [ ] 最近一次数据库备份可用（Database → Backups），或维护者已导出结构与数据。
- [ ] 线上 `AI_API_KEY` 仍有效、有余额（可在线上提交一道题看 AI 核对是否正常，或让维护者用同一密钥试调一次）。
- [ ] Vercel 已配置 `AI_KEY_SECRET`（32 字节 base64url），且线上未开启 `AI_ALLOW_SERVER_KEY`；否则用户无法保存自己的密钥。
- [ ] 维护者确认 `dev_ai` 已包含最新 `master` 且测试通过；Vercel 的 Production 分支是 `master`（以项目 Settings → Git 为准）。实际合并推送在 §3 执行。
- [ ] 已通知验收人员准备测试账号；发布窗口内不做题库导入、不改题面与答案（知识点标注除外）。

> 顺序不能颠倒：**先迁移数据库，再部署代码**。新代码会查询 `problems.knowledge_points`，先上代码会让题库页短暂 500。

## 1. 发布流程总览

```text
备份 → preflight 检查 → upgrade 升级 → postcheck 校验
     → 核对环境变量（见第 4 节；如需改，先改再推送，可少一次 Redeploy）
     → 合并推送，Vercel 自动构建
     → 本机标注知识点 → verify_knowledge 只读校验
     → 冒烟测试 → 签收
```

## 2. 数据库升级（Supabase SQL Editor）

三个文件都在 `supabase/release/`。每次都**整段粘贴**，不要只选其中几行。

发布包基于当前线上已有的 `202611060001_problem_text.sql`（文字题面）与 `20261110000000_ai_grading.sql`（AI 判分）；后者缺失时 preflight 会直接阻断，前者是文字题面的既有依赖。

这三个脚本已在本地 PostgreSQL 兼容环境（PGlite，最小前置表）实际跑通：preflight 判定、升级、postcheck 全项 PASS、重复执行被拦截、部分执行识别、缺前置时整段回滚。线上首次执行仍按 2.4 逐项核对。

### 2.1 备份

Supabase Dashboard → Database → Backups，记录最近一次备份时间；如项目启用了 PITR，记录当前恢复点。如果项目没有可用的自动备份（例如免费计划），让维护者在发布前用 `pg_dump` 导出（至少 `problems`、`problem_answers`；已有用户密钥时也包含 `user_ai_keys` 的密文），或确认可接受无备份窗口；没有可用备份时先不要执行。`AI_KEY_SECRET` 不在数据库里，单独保存在 Vercel 环境变量。

### 2.2 执行前检查

SQL Editor → New query → 粘贴 `supabase/release/20261113_preflight.sql` → Run。

预期最后一行：

| 序号 | 类别 | 迁移段 | 检查项 | 结果 | 说明 |
| --- | --- | --- | --- | --- | --- |
| 100 | 总体 | | 本发布状态 | 未执行 | 可整段执行 `supabase/release/20261113_upgrade.sql` |

同时「类别 = 前置」的 5 项必须全部为「通过」。

- 出现「阻断」：先补前置迁移（尤其 `20261110000000_ai_grading.sql`）或确认项目是否选错，**不要继续**。
- 出现「部分执行」：说明之前跑过其中几份迁移。按「迁移段」列的缺口，逐个执行 `supabase/migrations/` 下对应文件；不要执行整段 upgrade（开头保护段会直接报错）。
- 出现「已全部执行」：说明这 7 份迁移都已生效，跳过 2.3，直接做 2.4。

### 2.3 执行升级

新建查询，粘贴 `supabase/release/20261113_upgrade.sql` 全文 → Run。

预期显示 `Success` 且没有返回行，耗时数秒。

- 整个文件是一个事务：**任何一步失败都会整段回滚**，数据库保持执行前状态，不会留下半套表。
- 常见报错：
  - `permission denied`：当前账号不是项目 owner，用有 DDL 权限的账号。
  - `relation "public.problem_grading" does not exist`：前置迁移没执行，回到 2.2。
  - `检测到 2026-11-13 发布的对象已存在`：重复执行或部分执行，按 2.2 的「部分执行」处理。
- 失败的事务不会提交，数据保持在执行前状态；修好原因后可原样重跑。如果新的查询报 `current transaction is aborted`，说明失败的事务还挂在这个连接上：先单独执行 `rollback;`，或刷新页面重开 SQL Editor，再重跑。

### 2.4 执行后校验

新建查询，粘贴 `supabase/release/20261113_postcheck.sql` → Run。

预期最后一行：

| 序号 | 检查项 | 结果 | 说明 |
| --- | --- | --- | --- |
| 100 | 总体结果 | **PASS** | 结构检查全部通过，继续部署代码与知识点标注 |

28 项检查应全部为 `OK`。任何一项「未通过」都不要发布：按该行「说明」排查，必要时把整张结果表发给维护者。

迁移末尾的 `notify pgrst` 是异步生效的：紧接着访问接口时若暂时看不到新字段，等几秒或重新请求一次，不必重跑迁移。

### 2.5 记录

把 preflight 的「本发布状态」、upgrade 完成时间、postcheck 总体结果记到第 9 节签收表。

## 3. 部署代码（Vercel）

1. 维护者合并并推送 `master`（若尚未推送，见文末「维护者补充」）。第 4 节如需改环境变量，先改完再推送，可让本次部署直接生效，省一次 Redeploy。
2. Vercel → 项目 → Deployments，确认最新 Production 部署由本次提交触发、commit 与 `master` 最新提交一致，并处于 **Ready**。
3. 打开 `https://www.mathonline.top/api/v1/health`，应返回 `{"ok": true, "mode": "supabase", ...}`。
4. 打开 `https://www.mathonline.top/api/v1/catalog`，响应中应出现 `"knowledge": [...]`（知识点编码、中文名、题量）。
5. 登录后打开「我的学习 → AI 设置」，应出现密钥配置卡片；未配置密钥时 AI 功能返回 503 与「请先配置…」，不是 500 或白屏。
6. 浏览器强制刷新（Ctrl+F5）后再进入页面，避免旧前端缓存。

若本次只改了环境变量、没有新提交：Deployments → 最新生产部署 → Redeploy。**环境变量不会热更新，必须重新部署才生效。**

## 4. 环境变量（Vercel → Settings → Environment Variables，Production）

已有变量不要删。新增或修改下列项；若在推送代码前完成，随本次部署生效，否则保存后需 Redeploy：

| 变量 | 是否必填 | 建议值 | 说明 |
| --- | --- | --- | --- |
| `APP_MODE` | 已有 | `supabase` | 不改 |
| `SUPABASE_URL`、`SUPABASE_SERVICE_ROLE_KEY`、`SUPABASE_ANON_KEY` | 已有 | 不改 | 密钥只放服务端 |
| `APP_ORIGIN` | 已有 | `https://www.mathonline.top,https://mathonline.top` | 不改；顺序影响密码重置跳转 |
| `AI_BASE_URL` | 必填 | 现有值 | OpenAI 兼容基地址 |
| `AI_API_KEY` | 建议保留 | 现有值 | 标注脚本、管理员任务使用；用户功能默认使用各自密钥 |
| `AI_MODEL` | 必填 | `deepseek-chat` | 判分与标注默认使用；未单配蓝图模型时也用它 |
| `AI_KEY_SECRET` | **必填（用户密钥功能）** | 32 字节 base64url | 加密用户 API 密钥；缺失时用户无法保存，生成与轮换说明见下 |
| `AI_ALLOW_SERVER_KEY` | 可选 | 留空（线上关闭） | 设为 `on` 时，未配置密钥的用户会回退到 `AI_API_KEY`；按用户自带密钥模式应保持关闭 |
| `AI_TIMEOUT` | **建议新增或改为 120** | `120` | 默认 30 秒；实测生题单题约 37 秒，难题更久，30 会超时 |
| `AI_BLUEPRINT_MODEL` | 建议 | `deepseek-chat` | 组卷蓝图用快速模型，避免强推理模型超时 |
| `AI_VARIANT_MODEL` | 建议 | `deepseek-v4-pro` | AI 仿题用更强模型，提高一次通过率；留空则用 `AI_MODEL` |
| `AI_REASONING_EFFORT` | 可选 | 留空 | 只有思考型模型才需要设 `none` |
| `AI_VARIANT_SHARE` | 可选 | `on` 或留空 | `supabase` 模式默认开启共享题池 |
| `AI_VARIANT_POOL_SIZE` | 可选 | `3` | 每题最多保留 3 道仿题 |
| `AI_VISION_BASE_URL` | 选配 | 见下 | 图片作答转写；留空沿用 `AI_BASE_URL` |
| `AI_VISION_API_KEY` | 选配 | 见下 | 留空时：用户已配密钥则用用户密钥，否则沿用 `AI_API_KEY` |
| `AI_VISION_MODEL` | 选配 | 见下 | 必须是支持图片输入的模型 |
| `AI_VISION_TIMEOUT` | 选配 | `120` | 手写/多图可调高 |

用户 API 密钥的加密密钥：

```powershell
python -c "import os,base64;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
```

把输出填到 `AI_KEY_SECRET`，**只配置一次**。更换它会让所有已保存的用户密钥无法解密，用户必须重新保存；旧密文不会泄露明文，但会失效。密钥保存在服务端环境变量，不写进仓库、数据库或前端。

线上保持 `AI_ALLOW_SERVER_KEY` 关闭（留空即可），否则未配置密钥的用户仍会消耗服务端的 `AI_API_KEY`。

图片作答转写（选配）注意：

- 线上**不能**填本地 Ollama 地址（`127.0.0.1` / `localhost`），Vercel 访问不到。
- 不配置时，只传图片的作答仍会提交成功，但系统会先用 `AI_MODEL` 尝试转写：该模型不支持图片时会失败并降级为「待自检」，提示「图片作答转写暂不可用」；完全没配 AI 时才提示「图片作答转写未配置」。不会白屏、不会假报对错，但每次会多一次失败调用。
- 要启用就选一个支持 `/chat/completions` 且接受 `image_url` 的服务商，把 `AI_VISION_BASE_URL`、`AI_VISION_API_KEY`、`AI_VISION_MODEL` 配成该服务商的值（例如通义千问兼容模式、智谱 GLM-4V 等）。`AI_VISION_MODEL` 留空会沿用 `AI_MODEL`，所以必须显式配一个支持图片的模型；部署前先用一张作答图验证一次。
- 图片会发送给该服务商，确认其数据政策后再启用。

函数时长：

- Vercel 项目 Settings → Functions 确认已启用 Fluid Compute（新项目默认启用，函数默认上限 300 秒）。
- 若项目仍是非 Fluid 的旧版 Hobby 配置（上限 60 秒），先启用 Fluid；否则把 `AI_TIMEOUT` 降到 55 以内并用快速模型，否则组卷/仿题可能 504。

## 5. 知识点标注（维护者在本机执行）

这一步是**唯一会改线上题目元数据**的操作：只写 `knowledge_points`、`knowledge_source`、`difficulty` 三列，不递增版本、不改题面/答案/图片/学习记录。执行前确认已获授权。

它只依赖数据库和模型，不依赖新代码，因此也可以在迁移完成后、代码部署前先跑，缩短新功能上线后的空窗期；跑完再按第 3 节部署代码即可。

### 5.1 准备命令窗口

新开 PowerShell，在仓库根目录执行；密钥用隐藏输入，不写进命令历史：

```powershell
Set-Location "E:\GitRepo\mathonline"   # 改成部署机上的仓库路径

$env:APP_MODE = "supabase"
$env:SUPABASE_URL = "https://你的项目.supabase.co"
$env:SUPABASE_BUCKET = "mathonline"
$env:SUPABASE_SERVICE_ROLE_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'service_role key' -AsSecureString)).Password
$env:SUPABASE_ANON_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'anon key' -AsSecureString)).Password

$env:AI_BASE_URL = "https://api.deepseek.com"   # 与 Vercel 上的值保持一致
$env:AI_API_KEY = [System.Net.NetworkCredential]::new('', (Read-Host 'AI API key' -AsSecureString)).Password
$env:AI_MODEL = "deepseek-chat"
$env:AI_TIMEOUT = "90"
```

说明：仓库 `.env` 里的变量不会覆盖上面显式设置的值；不要把密钥写进 `.env` 提交。5.3–5.5 也在同一个窗口执行，换窗口会丢环境变量。

### 5.2 小样本干跑

```powershell
python -X utf8 scripts/tag_knowledge.py --dry-run --limit 20 --workers 2
```

预期：打印环境 `supabase`、题库总数、本次待标注数量、20 条示例（题目 ID → 知识点编码、难度、来源）。不写库。

### 5.3 正式全量标注

```powershell
python -X utf8 scripts/tag_knowledge.py --workers 4 --report output/knowledge-tag-report.json --strict-ai
```

- 只处理缺失标签的题；已标注的跳过。标注结果在全部跑完后按每 100 道一批写库：在 AI 阶段按 Ctrl+C 不会写入任何标签，重跑要重新调用全部模型；写库阶段若中途失败，已写入的题下次会被跳过。尽量一次跑完。
- 每 20 道打印一次进度；预计 20–40 分钟，模型费用通常几元到十几元，以账单为准。
- **不要先做全量 `--dry-run`**：干跑同样会真实调用模型，只是不写库，等于多花一倍费用。
- `--strict-ai` 的作用：配置了模型却没有任何 AI 标注时，在写完兜底结果后以退出码 2 提醒你密钥没生效；它不会阻止兜底写入，修好密钥后用 5.4 的 `--retag-heuristic` 补跑。
- 遇到 429/限流：把 `--workers` 降到 2 或 1 重跑。

### 5.4 补跑关键词兜底

```powershell
python -X utf8 scripts/tag_knowledge.py --retag-heuristic --workers 2
```

AI 失败时脚本会先用关键词兜底，保证每题至少一个标签；模型恢复后用这条命令只补跑兜底题。汇总里 `sources.ai` 应接近总题量。

### 5.5 只读校验（发布门禁）

```powershell
python -X utf8 scripts/verify_knowledge.py
```

期望：

- 退出码 `0`；
- 覆盖率 `100.0%`（`--json` 输出里 `coverage` 为 `1.0`）；
- 没有「检查未通过」提示（`--json` 里 `invalid` 为空）；
- 微积分上/下、线性代数、概率论显示「组卷准备 是」（JSON 里 `mock_ready` 为 `true`）。

任一项不满足：先不要开放考试预测，按输出里的未标注/非法编码清单补跑后再验。

### 5.6 标注结果存档

把 `output/knowledge-tag-report.json` 与 `verify_knowledge.py` 的输出存到发布记录里（`output/` 不进仓库）。

## 6. 冒烟测试（线上，验收人员执行）

冒烟前先登录一个普通账号，在「我的学习 → AI 设置」保存一个可用的 DeepSeek 密钥；第 11 项专门验证保存与删除。第 3 项用管理员账号，第 9 项按是否配置视觉模型执行，第 10 项用手机。

第 4 项需要该账号已有练习记录，第 7 项需要一道做错过的题，第 8 项需要两个普通账号。数据不足时先做几道题并故意答错一道，再继续。

| # | 操作 | 预期 | 不通过先查 |
| --- | --- | --- | --- |
| 1 | 题库页「知识点」下拉选「极限与连续」 | 结果都带该标签；选另一个知识点结果变化 | 迁移 / 标注未执行 |
| 2 | 随机练习选一个知识点后开始 | 下一题来自所选范围 | 前端缓存 / 接口 `knowledge=` 参数 |
| 3 | 管理员编辑一道题，勾选知识点保存 | 保存成功，版本号不递增，刷新后仍在 | 管理接口权限 |
| 4 | 我的学习 → 学情分析 | 显示最需加强、知识点正确率；点「生成 AI 诊断」得到报告，再点「重新生成」会重新调用模型 | `AI_*` 配置 / 练习数据不足 |
| 5 | 考试预测 → 微积分上 → 生成模拟卷 | 通常 8–20 题（最少 6 题）、约 100 分；至少一道选择/填空标「AI 仿题」；交卷前开发者工具里响应不含 `answer_text`/`explanation` | 迁移 003/120000、`AI_VARIANT_MODEL`、超时 |
| 6 | 在同一张卷上交卷 | 出现分数、解析、知识点得分；重复交卷被拒绝（409） | 判分接口 |
| 7 | 在一道做过且答错的题上「一题多练」 | 生成变式题，答案默认隐藏；点「查看答案与解析」后可见；自评后学情分析变式数增加 | 迁移 004、模型配置 |
| 8 | 账号 A、B 先后对同一道题点「一题多练」 | B 的响应带「共享题池」标记且秒出，题目与 A 相同；B 的自评不影响 A | 迁移 120000、`AI_VARIANT_SHARE` |
| 9 | （已配置视觉模型时）只上传作答图片、不填文字提交 | AI 核对面板出现「图片作答转写」，判分基于转写文字；同一修订号重复提交不重复调用 | `AI_VISION_*`、模型是否支持图片 |
| 10 | 手机打开首页与模拟卷 | 排版正常、按钮可点、公式不溢出 | 前端构建 / 缓存 |
| 11 | 我的学习 → AI 设置：保存一个可用密钥，再删除 | 保存后只显示末四位，接口响应不含完整密钥；删除后 AI 功能提示先配置密钥 | `AI_KEY_SECRET`、迁移 140000 |

未配置视觉模型时，第 9 项改为确认：提交成功、提示「图片作答转写暂不可用」（完全没配 AI 时为「未配置」）、页面不白屏。

发布后观察 1 天：Vercel 函数错误率与 504、模型调用费用、组卷与变式失败率；异常按第 7、8 节处理。

## 7. 回滚与降级

| 场景 | 操作 | 影响 |
| --- | --- | --- |
| 新代码有问题 | Vercel → Deployments → 上一个 Ready 版本 → Promote to Production（或 `git revert` 后重新部署） | 数据库结构保留，旧代码忽略新表新列 |
| 想停共享题池 | Vercel 设 `AI_VARIANT_SHARE=off` 并 Redeploy | 变式与模拟卷仍可用，只是每次重新生成 |
| 模型服务异常 | 无需改代码；组卷/变式返回 503，判分降级为待自检 | 不伪造结果 |
| 知识点标错 | 管理页逐题校正；或 `--retag` 重算 AI 标签 | 人工标注（`manual`）默认不被覆盖 |
| 用户密钥泄露/失效 | 用户自己在「我的学习 → AI 设置」删除或更换；服务端只存密文，管理员也看不到明文 | 删除后该用户的 AI 功能立即停用，旧密文不再被使用 |
| `AI_KEY_SECRET` 泄露或误换 | 轮换 `AI_KEY_SECRET` 并 Redeploy | 所有已保存密钥失效，用户需重新保存；项目不保留明文备查 |
| 想撤销本次结构 | **不推荐**：新表新列对旧代码无影响 | 迁移回退需单独评审，不在本手册承诺范围 |

代码回滚不会回滚数据库；回滚后已生成的模拟卷、变式题和历史记录仍保留。环境变量也不会随代码回滚，必要时手动改回。

## 8. 常见问题

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| 题库页 500，日志 `column problems.knowledge_points does not exist` | 迁移未执行或未提交 | 看 preflight 状态；补执行 upgrade 后重试 |
| 考试预测 503「AI 生题未配置…」 | `AI_*` 缺失或改后未 Redeploy | 补环境变量并重新部署 |
| 考试预测/变式一直失败，日志提示模型不存在 | `AI_VARIANT_MODEL` / `AI_BLUEPRINT_MODEL` 写了不可用的模型名 | 改成账号可用的模型，或留空回退 `AI_MODEL`，再 Redeploy |
| `/api/v1/health` 返回 `"mode": "local"` | Vercel 没设 `APP_MODE=supabase` | 补环境变量并 Redeploy |
| Vercel 构建失败 | 依赖或前端构建错误 | 看 Deployment Logs；本地用 `npm --prefix frontend run build` 复现 |
| 组卷或仿题 504 / 长时间无响应 | `AI_TIMEOUT` 太小、函数时长上限低、模型太慢 | 调到 120；启用 Fluid；用快速模型做蓝图 |
| 模拟卷 503「AI 生题未通过可解性校验」/ 变式 503「暂时无法生成可靠的变式」 | 模型多次求解不一致，或该题太难 | 换一道题重试；确认 `AI_VARIANT_MODEL` 生效；难题建议用更强模型 |
| 知识点下拉为空或没有题 | 迁移做了但标注没跑 | 执行第 5 节 |
| 图片作答一直「待自检」 | 未配视觉模型，或模型不支持图片 | 配置 `AI_VISION_*` 并用真实图片验证 |
| 登录/提交 403 | `APP_ORIGIN` 不含当前域名 | 补齐域名后 Redeploy |
| 共享题池不生效 | 迁移 120000 未执行或 `AI_VARIANT_SHARE=off` | 看 postcheck 第 4、8–10、15 项 |
| 所有 AI 功能 503「请先在…配置 DeepSeek API 密钥」 | 用户尚未保存自己的密钥；线上按设计不回退服务端密钥 | 提示用户到「我的学习 → AI 设置」保存 |
| 保存密钥 503「未配置密钥加密密钥」 | Vercel 缺 `AI_KEY_SECRET` 或格式/长度不对 | 按第 4 节生成 32 字节 base64url，配置后 Redeploy |
| 保存密钥 400「密钥无效」/「余额不足」 | DeepSeek 拒绝了密钥或余额不足 | 让用户检查密钥与账户余额后重试；服务端不会保存无效密钥 |
| 用户密钥突然全部「无法解密」 | `AI_KEY_SECRET` 被更换 | 这是预期行为；让用户重新保存密钥，或回滚到旧 secret（若仍持有） |

## 9. 发布记录（签收）

| 项目 | 记录 |
| --- | --- |
| 发布日期时间 | |
| 代码 commit | |
| Vercel 部署 ID / 时间 | |
| 数据库升级执行人 / 时间 | |
| 环境变量变更（如 `AI_TIMEOUT`、`AI_KEY_SECRET`） | |
| preflight 状态 | |
| upgrade 结果（成功/失败） | |
| postcheck 总体结果 | |
| 标注执行人 / 耗时 | |
| 标注汇总（总数 / AI / 兜底 / 覆盖率） | |
| `verify_knowledge.py` 退出码 | |
| 冒烟结果（通过 / 不通过项） | |
| 异常与处理 | |

## 附：文件清单

| 文件 | 用途 |
| --- | --- |
| `supabase/release/20261113_preflight.sql` | 执行前检查（只读） |
| `supabase/release/20261113_upgrade.sql` | 整段升级（单事务，生成文件，勿手改） |
| `supabase/release/20261113_postcheck.sql` | 执行后 28 项校验（只读） |
| `supabase/migrations/202611130001…20261113140000` | 权威迁移；部分执行时逐个补 |
| `backend/user_ai.py` | 用户密钥的 AES-GCM 加解密、掩码与校验 |
| `scripts/tag_knowledge.py` | 批量标注（AI 优先、关键词兜底） |
| `scripts/verify_knowledge.py` | 只读校验与组卷准备度 |
| `scripts/build_release_sql.py` | 迁移变更后重新生成 upgrade 脚本 |
| `docs/knowledge-analysis-mock-deployment.md` | 功能、接口、成本与风险详解 |

### 维护者补充：代码推送

发布前需要把本地 `dev_ai` 合并进 `master` 并推送（默认不自动推送，需明确授权）。`dev_ai` 已并入 `origin/master` 截至 2026-11-13 的文档提交；若远端之后又有新提交，按下面顺序先并入再合并，避免推送被拒：

```powershell
git status                     # 确认工作区干净，先提交或暂存未完成的修改
git fetch origin
git switch dev_ai
git merge origin/master        # 先并入远端提交；若有冲突按代码评审处理

# 合并后至少跑一次离线测试与前端构建
$env:APP_MODE = "local"; $env:LOCAL_DATABASE = "tmp/agent-tests.sqlite"
python -X utf8 -m pytest tests -q --basetemp tmp/agent-pytest
npm --prefix frontend run build

git switch master
git merge --ff-only origin/master
git merge --ff-only dev_ai
git push origin master

# 可选：同步 dev_ai 远端分支（不影响生产部署）
git push origin dev_ai
```

推送后由 Vercel 自动构建；确认 Production 部署 Ready，再继续第 3 节第 2 步起。若 `master` 开了分支保护，改走 PR 合并，合并后同样会触发生产部署。
