# Research Workbench 文档索引

系统设计与配套文档统一存放在项目根目录 `docs/`，同步日期：2026-10-08。

## 阅读顺序

1. [需求文档](01-requirements.md)：项目目标、用户场景、功能范围和验收标准。
2. [系统设计](02-system-design.md)：目标架构、Agent 编排、工具、数据模型、接口、恢复与安全设计。
3. [开发与评测计划](03-delivery-and-evaluation.md)：阶段规划、评测和简历作品交付。
4. [首版交付记录](04-development-status.md)：初次交付的历史快照。
5. [账号与会话设计](05-accounts-and-conversations.md)：多用户隔离、普通会话和管理员审阅。
6. [DeepSeek 接入说明](06-deepseek-integration.md)：模型配置、Tavily 搜索接入和流式输出。

## 设计与实现的区别

01–03 是开发前的目标设计，文首已标明；其中的 PostgreSQL、Celery、MCP、Langfuse、24 题消融、pnpm / Tailwind / Docker Compose 等**不代表当前仓库**。不要为这些后续项添加空目录。

当前实现是 Next.js（npm lockfile，样式在 `globals.css`）+ FastAPI + LangGraph `StateGraph` + SQLite + 进程内 `asyncio`；模型适配自写，不依赖 LangChain。已有根目录 `evaluations/`：10 题固定快照离线评测，见 [evaluations/README.md](../evaluations/README.md)。已实现范围、密钥连通记录与预算策略以[项目 README](../README.md)、[04 首版交付](04-development-status.md)（历史快照）、[05 账号与会话](05-accounts-and-conversations.md)、[06 DeepSeek 接入](06-deepseek-integration.md)、[07 预算与恢复](07-budget-and-recovery.md)为准。

当前联网搜索工具用于深度研究；普通会话调用模型并支持 POST SSE 流式显示，尚未接入联网搜索工具。密钥仅保存在本地配置中，不写入设计文档。

7. [预算与失败恢复](07-budget-and-recovery.md)：调用上限、报告预留、额度分配与页面提示。
8. [Langfuse 追踪](08-langfuse.md)：可选、默认关闭；一条 run 对应一条 trace。
