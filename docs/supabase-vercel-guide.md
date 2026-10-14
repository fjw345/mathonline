# Supabase + Vercel 部署指南

适用于当前项目，更新日期：2026-10-13。推荐一个 Vercel 项目同时提供 Vue 页面和 FastAPI 接口，Supabase 保存账号、题库、学习记录与图片。先使用 Vercel 提供的 HTTPS 域名，不需要购买服务器或域名。

当前代码已包含云端适配和迁移，但尚未在真实 Supabase/Vercel 项目验收。本指南是首次部署步骤，最后的验证环节必须实际完成。

## 1. 准备代码与账号

准备 GitHub、Supabase 和 Vercel 账号；本机安装 Python 3.10+、Node.js 20.19+ 和 npm。在 `D:\mathonline` 执行：

```powershell
python -m pip install -r requirements.txt
python -m pytest -q
npm.cmd --prefix frontend ci
npm.cmd --prefix frontend run build
```

将项目代码提交到 GitHub 仓库。包含 `frontend/`、`backend/`、`api/`、`requirements.txt`、`vercel.json` 与 `supabase/migrations/`。`api/index.py` 导出 FastAPI 的 `app`，不可留空。

`.env`、本地数据库、私人上传和 `node_modules` 不提交。现有 `.gitignore` 已排除它们。原卷和截图可留在本机用于离线导入，不需要随 Vercel 部署；不要把私人素材放进 `frontend/public/`。

这套步骤创建一套新的云端数据，不会迁移 SQLite 中的账号、密码或学习记录。需要保留已有本地数据时，先备份数据库、图片和清单；账号与历史迁移需另做专门方案。

## 2. 创建 Supabase 项目

在 [Supabase Dashboard](https://supabase.com/dashboard) 新建独立项目，选择便于目标用户访问的区域，保存数据库密码，等待项目准备完成。

在项目的 Connect 或 Settings/API 页面取得以下信息：

| 项目配置 | 当前代码的环境变量 | 用途 |
| --- | --- | --- |
| Project URL，形如 `https://xxx.supabase.co` | `SUPABASE_URL` | Auth、Data API 和 Storage 地址 |
| legacy `anon` key | `SUPABASE_ANON_KEY` | 邮箱密码认证请求 |
| legacy `service_role` key | `SUPABASE_SERVICE_ROLE_KEY` | 后端管理业务数据与私有图片 |

本版代码把 `service_role` 同时放入 `apikey` 和 Bearer Authorization，因此这里按 **legacy JWT keys** 配置。不要直接用 `sb_secret_...` 替换该变量；新式 secret key 不是 JWT，需要先调整服务端请求头并测试 Data API/Storage。Supabase 官方正在推动新式 publishable/secret keys，若新项目已无法取得 legacy keys，应先完成代码适配再部署。密钥值只填写到本机进程环境或 Vercel 环境变量，不写进代码、文档或前端 `VITE_*` 变量。

## 3. 按顺序执行数据库迁移

在 Supabase **SQL Editor** 中打开以下文件，逐个复制全文执行，确认成功后再执行下一个：

1. `supabase/migrations/202610100001_initial.sql`
2. `supabase/migrations/202610100002_answer_access.sql`
3. `supabase/migrations/202610110001_collection_filters.sql`
4. `supabase/migrations/202610120001_problem_feedback.sql`

这些脚本用于新项目初始化，不要反复执行第一份来“修复”错误；它包含 `create table`，重复执行会报对象已存在。后续升级只执行新增迁移。

第一份迁移会自动建立私有 `mathonline` Storage bucket，所以通常不需要手动再建。到 **Storage** 检查它存在且 **Public 关闭**；业务表启用 RLS，浏览器角色没有直接访问业务表的权限。不要添加允许任何人读取整个 bucket 的策略，图片由后端检查权限后签发短期链接。

最后一份迁移增加题目反馈表，并取消逐题审核作为发布门槛。已发布题目可进入练习池，缺少答案的素材仍可保留为草稿。

## 4. 从本机导入首批题库

离线导入会把原始截图上传到 Supabase Storage，再写入题库、答案和图片元数据，不经 Vercel 请求接口。

在 **新开的 PowerShell 窗口**中执行，避免影响正在运行的本地服务：

```powershell
Set-Location D:\mathonline
$env:APP_MODE = 'supabase'
$env:SUPABASE_URL = 'https://你的项目.supabase.co'
$env:SUPABASE_BUCKET = 'mathonline'

# 隐藏输入，避免把密钥明文写入命令历史。
$env:SUPABASE_SERVICE_ROLE_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '输入 service_role key' -AsSecureString)).Password
$env:SUPABASE_ANON_KEY = [System.Net.NetworkCredential]::new('', (Read-Host '输入 anon key' -AsSecureString)).Password

python -X utf8 scripts/import_bank.py data/nonmath-manifest.json
```

导入前本机必须仍有清单 `data/nonmath-manifest.json` 及其 `source_path` 指向的 `devided-math` 原始截图。不要只复制 JSON 后删除截图，也不要拿管理页导出的空资产清单初始化一个全新的图片库。

全新库预期新增 222 道题、500 个图片资产；没有人工修改时再次导入应报告 222 道内容不变。网页人工改过的题目默认受保护，不使用 `--override-manual`。当前导入流程不把本地管理员的修改自动同步到云端，也不迁移个人上传。

在 SQL Editor 检查：

```sql
select status, count(*) from public.problems group by status;
select count(*) as answer_count from public.problem_answers;
select count(*) as image_count from public.media_assets;
```

首批清单预期 222 道已发布题目、222 条答案和 500 个图片资产。同时在 Storage 检查图片对象已经上传。导入完成后关闭此 PowerShell 窗口，后续仍可正常使用原本的本地启动器。

## 5. 创建 Vercel 项目

在 [Vercel Dashboard](https://vercel.com/dashboard) 选择 **Add New → Project**，导入 GitHub 仓库。

| 设置 | 值 |
| --- | --- |
| Root Directory | 仓库根目录，不能选 `frontend` |
| Framework Preset | Other，以仓库 `vercel.json` 为准 |
| Build Command | `npm --prefix frontend ci && npm --prefix frontend run build` |
| Output Directory | `frontend/dist` |
| Install Command | 可设 `npm --prefix frontend ci`；构建命令也会执行安装 |

根目录保留 `pyproject.toml`、`uv.lock` 和 `.python-version`。Vercel 优先通过 `pyproject.toml` 的 `[project]` 读取依赖，使用 Python 3.12；该文件不能只有 Ruff 配置。`requirements.txt` 仍用于本地启动器。不要用本地 `scripts/start_local.py` 启动云端，也不要在云端使用 SQLite。

在 **Settings → Environment Variables** 设置以下变量，首次只对 **Production** 生效：

```dotenv
APP_MODE=supabase
APP_ORIGIN=https://你的项目.vercel.app
SUPABASE_URL=https://你的项目.supabase.co
SUPABASE_SERVICE_ROLE_KEY=你的legacy-service-role-key
SUPABASE_ANON_KEY=你的legacy-anon-key
SUPABASE_BUCKET=mathonline
LOCAL_PREVIEW_DRAFTS=false
```

先确定 Vercel 的固定 Production Domain，例如 `mathonline-demo.vercel.app`，然后将 `APP_ORIGIN` 替换为该域名的完整 HTTPS 地址，不带路径或结尾斜杠。首次创建时若尚不确定，可先部署取得域名，再改变量并 **Redeploy**。环境变量修改不作用于已运行的旧部署。

不设置 `LOCAL_DATABASE`。如使用自有域名，`APP_ORIGIN` 可逗号分隔允许的域名，常用主域名放第一位，例如 `https://math.example.com,https://mathonline-demo.vercel.app`；第一项也用于密码重置跳转。只读页面能打开不代表来源配置正确，错误来源会让登录、提交等操作返回 403。

当前 `vercel.json` 将 `/api/v1/*` 转给 Python 入口，其余页面交给 Vue。部署后需要验证 API 收到的完整路径仍是 `/api/v1/...`，而不是 `/api/index`。浏览器和 API 使用同一域名，保留 HttpOnly Cookie；不要另把前端 `/api` 地址改成 Supabase 地址。

不要把 Production 密钥直接复制到所有 Preview 部署。后续需要预览环境时，另建 Supabase 测试项目，配置固定测试域名、Preview 环境变量和认证回调。

## 6. 配置邮箱注册和密码重置

Supabase **Authentication → URL Configuration**：

- **Site URL**：`https://你的项目.vercel.app/auth`
- **Redirect URLs**：加入 `https://你的项目.vercel.app/auth` 和 `https://你的项目.vercel.app/auth?mode=recovery`

这里 Site URL 带 `/auth` 是因为当前前端只在该页面读取认证链接的 token；注册接口没有另传跳转地址。邮件模板保留 Supabase 标准的 `{{ .ConfirmationURL }}` 链接流程，不要自行改成当前代码尚未实现的 `token_hash` 验证流程。

在 Authentication 的 Email 设置中启用邮箱注册，并选择是否要求 Confirm email。面向真实用户建议启用验证，并配置可用的自定义 SMTP：Supabase 默认邮件服务有收件人和发送频率限制，不能据此假设所有用户都能收到邮件。

首次实测注册邮件：打开链接后应进入 `/auth`，完成登录并进入个人页；再测试“忘记密码”邮件是否进入 `/auth?mode=recovery`，修改密码后可重新登录。链接失效时重新发起操作；不要把验证邮件中带 token 的完整地址分享给别人。

## 7. 设置云端管理员

云端注册的第一个用户**不会自动成为管理员**。先在网站注册、验证邮箱并至少成功登录一次，确保后端创建了 `public.profiles` 记录；只在 Supabase Auth 后台新建用户还不够。

然后在 SQL Editor 运行，替换为自己的邮箱：

```sql
select id, email, nickname, role
from public.profiles
where email = '你的管理员邮箱';

update public.profiles
set role = 'admin'
where email = '你的管理员邮箱';
```

确认更新恰好一行，刷新网页或重新登录，侧栏应出现“管理”。管理员可以扩充和修改题库、处理题目反馈及分配角色。社区审核人员可设为 `moderator`，他们仅审核题解与评论，不处理题库反馈。

## 8. 部署后的验证

先打开以下地址：

```text
https://你的项目.vercel.app/api/v1/health
https://你的项目.vercel.app/api/v1/catalog
```

健康接口应返回 JSON，包含 `ok: true` 和 `mode: "supabase"`；题库接口预期返回 222 道题及“全国大学生数学竞赛·非数”标签。若返回 HTML，先修复路由，不能继续把它当作接口正常。

接着使用普通用户和管理员账号实测：

1. 首页加载题库和图片；直接打开并刷新 `/practice`、`/daily`、`/admin` 页面。
2. 注册、验证邮件、登录、退出、会话刷新和密码重置。
3. 未作答时答案锁定；提交或点击“不会”后解锁；刷新后默认折叠。
4. 文字作答、自检、错题、每日题手动打开和打卡；随机题和草稿在页面返回后恢复。
5. 上传小图片并查看自己的作答；另一个账号不能读取私人作答或未解锁答案。
6. 提交题目反馈，管理员在“题目反馈”查看、修改题目并标记处理结果。
7. 新增和修改题目、图片顺序、下架与重复导入；学习记录仍关联原稳定 ID。
8. 题解发布、审核和评论；再用手机及目标校园网检查图片加载和操作。

Vercel **Logs/Functions** 查看 Python 异常；Supabase 查看 Auth、Database 和 Storage 日志。记录实际失败的接口、状态码与时间，不把密钥、Cookie 或邮件 token 贴进公开问题单。

## 9. 当前云端限制与排错

| 现象 | 检查方向 |
| --- | --- |
| 构建成功，API 404 或返回 HTML | 根目录是否正确、`api/index.py` 是否导出 app、API rewrite 是否生效及保留原路径 |
| Python 函数 bundle 超过大小上限 | 保留 `vercel.json` 中 `functions.api/index.py.excludeFiles`，排除原卷、拆分截图、前端及本地数据目录；题图通过离线导入上传 Supabase，不打包到函数 |
| API 500，日志提到 SQLite 或只读目录 | `APP_MODE` 是否为 `supabase`，变量是否属于当前部署环境，改完是否 Redeploy |
| 表或 RPC 不存在 | 四份迁移是否在同一个项目按顺序执行，URL 与 key 是否属于该项目 |
| 登录或提交返回“请求来源不被允许” | `APP_ORIGIN` 是否包含当前页面完整 HTTPS origin，自有域名是否已加入 |
| 邮件验证后回到首页却没有登录 | Site URL 是否为 `/auth`，邮件模板是否仍使用标准确认链接 |
| 没有管理入口 | 是否成功登录生成 profile、是否只修改了 Auth 用户而没更新 profiles.role |
| 题库为空 | 离线导入是否成功、状态是否为 published、是否查看了另一个 Supabase 项目 |
| 图片 404/403 或无法加载 | bucket 名称、对象上传、签名请求权限、答案是否解锁、用户是否仍登录 |
| 上传返回 413 | 当前文件经 Vercel 函数中转，触发请求体上限；先压缩图片 |
| 用户提交超时 | 云端网络、函数冷启动或多次 Data API 请求；检查日志和数据库区域，不只反复刷新 |

当前上传接口虽然允许单张 5 MB，Vercel 官方函数请求/响应体限制为 **4.5 MB**，multipart 本身还有额外开销。因此当前上线试用请先使用约 **3 MB 以下**的图片；界面的 5 MB 上限不能视作云端保证。要稳定支持原有 5 MB，应开发 Storage 签名直传与上传确认接口，或同步降低前后端上传上限。离线题库导入直接上传 Storage，不受 Vercel 请求体限制。

当前云端业务存在逐条 Data API 查询，完整首批题库管理与图片请求可能比本地慢；前端普通请求有 20 秒超时，上传有 60 秒超时。还需真实验证函数耗时、会话刷新、邮件和社区并发操作。不要在 Vercel 临时磁盘保存持久数据，也不要直接把本地 28 项测试通过等同于线上已验收。

## 10. 后续更新与备份

代码修改推送 GitHub 后由 Vercel 重新部署；有新数据库迁移时，先确认兼容关系，再执行新增脚本。新题库使用稳定 ID、真实图片映射和类别标签导入，管理员人工修改默认不会被批量导入覆盖。

备份 Supabase 数据库、Storage 图片和导入清单；只导出题库 JSON 不包含账号、学习记录和图片文件。Vercel 回滚只回滚应用部署，不会回滚 Supabase 数据或迁移，错误数据修复需单独处理。

## 官方参考

- [Vercel Python Runtime](https://vercel.com/docs/functions/runtimes/python)
- [Vercel Function Limits](https://vercel.com/docs/functions/limitations)
- [Supabase API Keys](https://supabase.com/docs/guides/api/api-keys)
- [Supabase Redirect URLs](https://supabase.com/docs/guides/auth/redirect-urls)
- [Supabase Email Auth](https://supabase.com/docs/guides/auth/auth-email)
- [Supabase Custom SMTP](https://supabase.com/docs/guides/auth/auth-smtp)
