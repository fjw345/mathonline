# 本地运行与后续部署

首次部署请按 [Supabase + Vercel 逐步指南](supabase-vercel-guide.md) 操作，包含迁移顺序、环境变量、图片导入、邮件回调、管理员设置和上线检查。

## 当前本地模式

`APP_MODE=local` 使用 SQLite、磁盘图片与本地邮箱密码账号，不依赖 Supabase/Vercel。服务默认只监听 `127.0.0.1:8011`。注册/login 会创建 HttpOnly 会话 Cookie，密码经 scrypt 加盐散列存储。首次账号的管理员分配在数据库事务内完成，无默认管理员口令。

`.env.example` 可复制为 `.env` 调整配置；不要提交 `.env`、数据库或私人上传。关闭终端停止服务，重新运行启动器保留数据。前端构建输出由 FastAPI 统一提供，开发时 Vite 将 `/api` 代理至后端。

## 备份

建议停止本地服务后，一并备份以下目录/文件：

- `data/mathonline.sqlite`（若存在 `-wal`/`-shm` 文件，停止服务并确认连接已关闭后再复制）。
- `data/assets/`、`data/uploads/`、`data/nonmath-manifest.json`。
- 原始 `devided-math/`、`resource/` 及代码。

恢复时使用同一份数据库和资产副本，启动器不会清空它们。不要将后台 JSON 导出当作账号/作答/图片的完整备份。当前尚未实施定期备份调度或恢复演练。

## Supabase/Vercel 后续接入

已提供 Supabase Data API、Auth、私有 Storage 适配器及 `supabase/migrations/` 下的 SQL 迁移，但当前没有远程项目，未在真实云端执行迁移或部署。以下配置是后续接入入口，不能当作线上验收完成。

1. 在独立 Supabase 项目按文件名时间顺序执行全部迁移，创建私有 `mathonline` Bucket。业务表启用 RLS，撤销浏览器角色直接访问；所有业务由后端检查权限，服务端凭证可绕过 RLS。
2. 服务端设置 `APP_MODE=supabase`、`SUPABASE_URL`、`SUPABASE_SERVICE_ROLE_KEY`、`SUPABASE_ANON_KEY`、`APP_ORIGIN=https://实际网站域名`。密钥留在服务端。若使用新的 secret key，确认所选 Storage/Data API 的 Authorization 格式后再替换现有 service role 配置。
3. 使用同一清单导入资产与题库，首批非数题目默认发布，不要求逐题预审。用户反馈进入管理员的“题目反馈”列表，核查后修改或下架有问题的题目。暂无答案的素材可保存为草稿。
4. Supabase Auth 中配置站点 URL、邮箱验证回调和 `/auth?mode=recovery` 重置地址；开启邮箱验证、配置可用邮件发送服务。注册后用本地管理命令或服务端维护流程为指定 Supabase 用户分配管理员角色，远程注册不会自动获得管理权限。
5. 前端用 `npm run build` 静态部署；FastAPI 后端按托管平台 Python runtime 设置入口。将同域 `/api/*` 代理到后端，保留 Cookie、Origin 与查询参数，避免把业务 API 路径重写到 SPA。可使用提供的根目录 `vercel.json`/`api/index.py` 作为单项目部署入口，首次远程发布仍需实测兼容性与限额。
6. 验证校园网和移动网络下登录、邮件、图片、上传和完整核心流程，再对外发布。

## 当前边界

本地图片上传通过 FastAPI Multipart 接收，单张最多 5 MB。该路径适合本地 MVP；Vercel 请求体限制可能小于此值，云端对用户上传尚需改为 Storage 签名直传或降低上限并实测，不能直接承诺 5 MB 云端上传可用。题库的离线导入已直接上传 Storage，不经过用户请求。

共享数据库计数限制认证/写入频率，当前是基础限制，不具备完整运营级防刷能力。私人作答只对本人开放；待审核题解可供作者与审核人员查看，审核通过才向其他人展示。正文以 Vue 文本节点渲染，不执行用户 HTML 或公式代码。

已实现本地作答、打卡、题目维护、社区提交、审核及点赞通知的数据库事务/版本检查。云端社区提交、通知生成、审核日志仍需进一步完善并发事务和重试恢复；站内消息当前在进入页面时刷新。未实现独立上传凭证/确认接口、自动清理孤立文件、账号删除、后台定时备份、邀请奖励、AI 判题或完整题面文本无障碍支持。这些不阻塞本地验证，但需纳入公开上线前评估。

