# 知识点、AI 学情、考试预测与一题多练：数据与上线指南

本页对应 2026-11-13 这一轮需求（知识点标签、AI 薄弱分析、北交考试预测/模拟卷、错题 AI 变式）。全文区分三种状态：

- **已实现**：代码在仓库中，离线测试覆盖；
- **本地已验证**：本轮在本地 SQLite + 本地模型/测试替身上实际执行过；
- **未执行**：需要线上凭证或生产数据的操作，本轮没有做。

> 线上数据没有在本轮改动。下面的迁移、标注与冒烟步骤需要在明确授权后由维护者执行。

## 1. 本轮交付一览

| 功能 | 主要代码 | 数据 | 状态 |
| --- | --- | --- | --- |
| 知识点标签与筛选 | `backend/topics.py`、`backend/tagging.py`、`backend/domain.py`、`backend/store.py`、`frontend/src/views/Library.vue`、`frontend/src/components/PracticeFilters.vue` | `problems.knowledge_points`、`problems.knowledge_source`、`problems.difficulty`（内部） | 已实现；本地 774 道全部标注 |
| AI 学情分析 | `backend/analysis.py`、`main.py` 的 `/me/analysis`、`frontend/src/views/Learning.vue` | `learning_analyses` | 已实现；统计离线验证，报告离线用替身验证 |
| 考试预测与模拟卷 | `backend/mockexam.py`、`backend/question_gen.py`、`main.py` 的 `/mock/*`、`frontend/src/views/Mock.vue`、`MockPaper.vue` | `mock_papers` | 已实现；离线全流程验证，本地模型实测生成过整卷 |
| 错题 AI 变式 | `backend/variants.py`、`main.py` 的 `/me/.../variants`、`frontend/src/views/Problem.vue` | `practice_variants` | 已实现；离线全流程验证 |

## 2. 结构变更

### 2.1 新列（`problems`）

| 列 | 类型 | 说明 |
| --- | --- | --- |
| `knowledge_points` | jsonb / TEXT '[]' | 知识点编码数组，例如 `["limit","derivative"]`，最多 4 个 |
| `knowledge_source` | text / TEXT '' | `ai`、`heuristic`、`manual` 或空；人工标注默认不被脚本覆盖 |
| `difficulty` | 既有列 | 一直存在；现在由标注脚本写入 1–5，**只用于组卷与统计，不在学生页面展示**，也不作为公开筛选维度 |

知识点编码表在 `backend/topics.py`，按学科限制可选范围（微积分上/下、高等数学、线性代数、概率论）。编码一经上线不要再改名；新增知识点只追加。

### 2.2 新表

| 表 | 用途 | 关键列 |
| --- | --- | --- |
| `learning_analyses` | 每人一份 AI 诊断缓存 | `id`（用户 ID）、`fingerprint`（练习数据指纹）、`stats`、`report`、`model`、`created_at` |
| `mock_papers` | 每人每张模拟卷一行 | `items`（题目快照与判分结果）、`blueprint`（预测蓝图）、`profile`（往年分析）、`knowledge_summary`、`status`、`score`、`max_score` |
| `practice_variants` | 错题变式题 | `problem_id`（原题）、`question_text`、`answer_text`、`correct_options`、`result`、`answered_at` |

模拟卷与变式题都保存题目快照，原题后续改版不会改变已生成内容；AI 题不写回公开题库。

### 2.3 迁移顺序

按文件名顺序执行，均为纯新增：

1. `supabase/migrations/202611130001_knowledge_points.sql`
   - 加两列与 GIN 索引；
   - 重定义 `save_problem`：旧客户端不传知识点时不覆盖已有标签，difficulty 不再被导入清空；
   - 新增 `apply_problem_knowledge(jsonb)`，只写三个元数据列。
2. `supabase/migrations/202611130002_learning_analysis.sql`
3. `supabase/migrations/202611130003_mock_exams.sql`
4. `supabase/migrations/202611130004_practice_variants.sql`

每份迁移都以 `notify pgrst, 'reload schema'` 结束，并只授权 `service_role`。执行前确认目标项目；执行后核对表存在、RLS 开启、`anon`/`authenticated` 无权限。

## 3. 上线顺序

1. **备份**：按既有流程备份当前数据库（至少 `problems`、`problem_answers`）。
2. **执行迁移**：按第 2.3 节顺序执行。迁移是纯新增，旧代码会忽略新列/新表，因此**建议先迁移、再部署代码**，避免新代码查询新列时出现短暂报错。
3. **部署代码**：部署后端与前端。迁移未执行时：知识点筛选为空、学情分析只显示统计、模拟卷/变式返回 503，均有明确提示。
4. **配置模型**（第 4 节）：至少设置 `AI_BASE_URL`、`AI_API_KEY`、`AI_MODEL`；重新部署或重启 API。
5. **标注知识点**（第 5 节）：先 `--dry-run` 看抽样，再正式写入。**这是唯一会改线上 `problems` 元数据的步骤**，只写 `knowledge_points`、`knowledge_source`、`difficulty`。
6. **只读校验**：`python -X utf8 scripts/verify_knowledge.py`。
7. **冒烟**：按第 7 节逐项检查。
8. 观察一天后再考虑把 `AI_REASONING_EFFORT`、并发数按实际服务商调整。

## 4. 模型配置

沿用既有 `AI_*`（见 [AI 自动核对答案](ai-grading.md)），本轮**没有新增必填变量**。

| 变量 | 说明 |
| --- | --- |
| `AI_BASE_URL` | OpenAI 兼容基地址，实际请求 `{基地址}/chat/completions` |
| `AI_API_KEY` | 只放服务端环境变量；不要提交、不要放前端 |
| `AI_MODEL` | 标注、蓝图、生题、判分共用的模型名，写入各表的 `model` 列 |
| `AI_TIMEOUT` | 单次请求超时秒数；生题建议不低于 60 |
| `AI_BLUEPRINT_MODEL`（可选） | 考试预测蓝图单独使用的快速模型；留空沿用 `AI_MODEL`。强推理模型在长提示上容易超时或输出截断，建议蓝图固定用快速模型 |
| `AI_REASONING_EFFORT`（可选） | 思考型模型可设为 `none` 关闭思考，避免推理占满输出预算；留空则不发送该字段 |

建议：

- **批量标注**用便宜、稳定的非思考模型（例如 `deepseek-chat`）即可；线上全量约 1,900 道题，约需 1,900 次调用，按提示词长度估算成本很低。
- **生题/校验**建议用更强的模型；同一模型既命题又自校验会共享盲区，这是已知风险（第 8 节）。
- 若生题使用强推理模型（如 `deepseek-v4-pro`），把 `AI_BLUEPRINT_MODEL` 设为 `deepseek-chat`：蓝图快且稳定，生题仍走强模型。实测 `deepseek-chat` 的仿题与校验质量已能满足基础题，整卷约 10–30 秒；`deepseek-v4-pro` 更强但单题约 1 分钟。
- 本地 Ollama 演示可设 `AI_BASE_URL=http://127.0.0.1:11434/v1`、`AI_MODEL=qwen3.5:9b`、`AI_REASONING_EFFORT=none`。9B 模型能跑通全流程，但生成质量明显弱于线上模型，不建议作为生产选择。

## 5. 知识点标注

脚本：`scripts/tag_knowledge.py`。

```powershell
# 1) 先干跑：不写库，只看抽样与来源分布
$env:APP_MODE = "local"   # 线上改为 supabase，并配置 SUPABASE_*
python -X utf8 scripts/tag_knowledge.py --dry-run --limit 20 --workers 4

# 2) 本地正式写入（只补缺失，人工标注不动）
python -X utf8 scripts/tag_knowledge.py --workers 4

# 3) 线上：先干跑全量，再正式执行；务必确认 APP_MODE 与目标项目
$env:APP_MODE = "supabase"
python -X utf8 scripts/tag_knowledge.py --dry-run
python -X utf8 scripts/tag_knowledge.py --workers 4 --report output/knowledge-tag-report.json

# 4) AI 失败后只补跑关键词兜底的题目
python -X utf8 scripts/tag_knowledge.py --retag-heuristic --workers 4
```

行为与保护：

- 只写 `knowledge_points` / `knowledge_source` / `difficulty`，不递增题目版本、不改题面/答案/图片/学习记录。
- 默认只处理缺失标签的题；`--retag` 重算已有 AI 标签；`--retag-heuristic` 只补跑关键词兜底；`--override-manual` 才会覆盖人工标注（慎用）。
- 模型不可用、请求失败或返回越界编码时自动回落到关键词规则，保证每道题至少一个知识点；来源标为 `heuristic`，便于事后筛选补跑。
- 失败不会中断整批：429/超时会重试，仍失败就按兜底写入并在汇总里计数。
- 汇总输出 `processed / sources / difficulty / coverage / invalid / updated`，`--report` 可落盘 JSON。
- 并发默认 4；服务商限流时降到 2 或 1。脚本不打印密钥。

本轮本地执行结果（2026-11-13，774 道）：596 道 AI 标注、178 道关键词兜底、覆盖率 100%、非法编码 0；微积分上/下、线性代数、概率论、高等数学五类均达到组卷题量要求。

## 6. 接口与权限

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| GET | `/api/v1/catalog` | 新增 `knowledge`：编码、中文名、领域、题量 |
| GET | `/api/v1/problems?knowledge=code` | 知识点筛选（逗号分隔为 ANY）；非法编码返回空 |
| GET | `/api/v1/admin/problems` | 管理列表新增 `knowledge_points`（解析后）与 `knowledge_codes`（原始编码）、`difficulty` |
| PUT | `/api/v1/admin/problems/{pid}/knowledge` | 管理员校正知识点与内部难度；不递增版本，写 `knowledge_source=manual` |
| GET | `/api/v1/me/analysis` | 现算统计 + 缓存报告；只返回自己的数据 |
| POST | `/api/v1/me/analysis[?refresh=true]` | 生成/刷新 AI 报告；限 6 次/分钟；未配置模型返回 503 |
| GET | `/api/v1/mock/subjects` | 公开的往年结构统计与组卷准备度；不需要登录 |
| POST | `/api/v1/mock/papers` | 组卷；限 3 次/2 分钟；AI 未配置或生题校验失败返回 503 |
| GET | `/api/v1/mock/papers` / `/{id}` | 只返回自己的卷；未交卷时答案与解析不下发 |
| PUT | `/api/v1/mock/papers/{id}/submission` | 交卷判分；重复交卷 409 |
| PATCH | `/api/v1/mock/papers/{id}/items/{item_id}` | 仅对 `pending` 题自评；重算总分与知识点统计 |
| DELETE | `/api/v1/mock/papers/{id}` | 删除自己的模拟卷 |
| POST | `/api/v1/me/problems/{pid}/variants` | 生成变式题；限 6 次/分钟；答案不随响应下发 |
| GET | `/api/v1/me/variants[/{id}/answer]` | 列表（可带 `problem_id`）与查看答案；只能访问自己的记录 |
| PATCH | `/api/v1/me/variants/{id}` | 自评 `correct`/`incorrect`；重复自评 409 |

所有 `/me/*`、`/mock/papers*` 都以登录身份为准，不接收前端传入的用户 ID；模拟卷答案与变式答案都由服务端在解锁前隐藏，不能靠前端折叠代替权限。

## 7. 校验与冒烟

### 7.1 只读数据核对

```powershell
$env:APP_MODE = "local"
python -X utf8 scripts/verify_knowledge.py          # 有未标注/非法编码时退出码 1
python -X utf8 scripts/verify_knowledge.py --json   # 完整报告
```

期望：覆盖率 100%，`invalid` 为空，微积分上/下、线性代数、概率论 `mock_ready=true`。

### 7.2 离线测试

```powershell
$env:APP_MODE = "local"; $env:LOCAL_DATABASE = "tmp/agent-tests.sqlite"
python -X utf8 -m pytest tests --basetemp tmp/agent-pytest
```

本轮全量 136 项通过；新增覆盖：知识点编码/筛选/人工校正/标注脚本、学情聚合/报告校验/缓存/降级、蓝图/组卷/AI 题校验/交卷判分、变式生成/答案隐藏/越权。

### 7.3 线上冒烟清单

1. 题库页出现“知识点”下拉，按“极限与连续”筛选，结果均带该标签；非法编码不返回全库。
2. 随机练习/每日一题可选知识点，应用后下一题来自该范围。
3. 管理员编辑题目时勾选知识点并保存，版本号不递增，刷新后仍生效。
4. 我的学习 → 学情分析：有练习数据时显示最需加强、正确率表；点“生成 AI 诊断”得到报告；再点“重新生成”会重新调用模型（观察 `learning_analyses.model` 与时间戳变化）。
5. 考试预测：选“微积分上”生成模拟卷，得到 8–20 题、约 100 分；至少一道选择题或填空题标有“AI 仿题”；交卷前接口响应中不含 `answer_text`/`explanation`；交卷后出现分数、解析与知识点得分；重复交卷返回 409。
6. 一题多练：在做过且答错的题上生成变式，接口响应不含答案；点“查看答案与解析”后才出现；自评后学情分析的 `variant_totals` 增加。
7. 未配置 AI 的环境：学情分析只显示统计并提示未配置；组卷与变式返回 503；页面不白屏、不假报成功。

## 8. 回滚与风险

- **代码回滚不回滚数据库**：新列/新表对旧代码是可忽略的附加数据；旧版本忽略它们即可。不要为了回滚删除学习记录或模拟卷。
- **知识点标注回滚**：只影响三列。需要重新标注时直接重跑脚本（`--retag` / `--retag-heuristic`）；需要清空某题时管理页取消勾选。`manual` 来源默认不会被脚本覆盖。
- **迁移本身的回滚**不在本指南承诺范围；如确需回退结构，请先在测试项目演练并单独评审。
- **AI 标注可能不准确**：模型可能把综合题标偏，管理员可在编辑页校正；关键词兜底只保证“有标签”，不保证“最准确”。
- **AI 生题不是数学证明**：现有校验是“模型独立求解 + 答案一致性 + 解析最终结论与 answer 字段一致”，能挡住明显无解/答非所问/与原题雷同/答案与解析自相矛盾，但同模型自校验会共享盲区。生产建议：用更强模型；抽检 AI 题；保留用户反馈入口；不要承诺 AI 题 100% 正确。AI 题不进公开题库，正是为了避免未审核题目污染题库。
- **成本**：一次组卷通常 3–6 次模型调用（蓝图 + 命题 + 校验，失败会重试）；一次变式 2–4 次；一次学情报告 1 次。接口层已有限流，但批量标注会集中消耗额度，建议分批、观察用量。
- **思考型模型**：可能把输出预算花在推理上，`ai.py` 会检测“只有 reasoning_content”并追加预算重试；若仍为空则按不可用降级，不写入半成品。
- **本地模型实测**：qwen3.5:9b 可跑通组卷全流程，但生成题质量不稳定（例如答案算错、选择题表述不严谨），本轮把它作为演示与下限验证；线上请使用更强模型并抽检。
- **本轮未做**：线上迁移、线上标注、线上冒烟、真实手机验收、AI 题人工全量审核、模型成本实测。`docs/` 之外的设计文档未改。

## 9. 相关命令速查

```powershell
# 标注（本地/线上由 APP_MODE 决定）
python -X utf8 scripts/tag_knowledge.py --dry-run --limit 20
python -X utf8 scripts/tag_knowledge.py --workers 4 --report output/knowledge-tag-report.json
python -X utf8 scripts/tag_knowledge.py --retag-heuristic

# 只读核对
python -X utf8 scripts/verify_knowledge.py

# 离线测试
$env:APP_MODE = "local"; $env:LOCAL_DATABASE = "tmp/agent-tests.sqlite"
python -X utf8 -m pytest tests --basetemp tmp/agent-pytest

# 前端
npm --prefix frontend run build
```
