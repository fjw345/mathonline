# 仓库代理入口

先阅读 [AGENT.md](AGENT.md)，遵循其中的项目背景、业务规则、验证及数据保护约定。

## 文档读取

Whenever a PDF, image, or Office document needs to be read or parsed, prefer the `mineru` Skill.

仓库内 skill：[.agents/skills/mineru/SKILL.md](.agents/skills/mineru/SKILL.md)。处理支持的文档格式前读取它；Markdown 和源代码直接读取。

- 此副本来自本机已安装的 MinerU skill，2026-10-30 纳入版本管理；仅将一处未随原 skill 分发的上游文档链接改为指向说明文字，命令及工作流程保留。
- Skill 是代理操作说明，实际解析需要独立安装 MinerU CLI `>=4.0,<5`。本次不安装程序、模型或解析服务，也不将它们加入网站 Python 依赖；按 skill 的安装与版本检查步骤配置维护者环境。
- 默认本地解析，远程上传仍需用户明确授权。不得提交 API key、个人配置、解析缓存、模型或私人文档。
- 沿用 `output/` 保存需落盘的解析产物；不要改变现有题库截图导入及展示方式。
- 仓库级 skill 可随 Git 克隆携带；工具是否自动发现 `.agents/skills` 取决于其版本，不自动发现时使用上述路径显式读取。
