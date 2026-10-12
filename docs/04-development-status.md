# Deep Research 工作台首版开发交付

> 历史记录：本文描述首次交付时的状态。后续已加入账号权限、普通会话、管理员审阅、DeepSeek/Tavily 真实调用及前端逐字流式展示；请结合 05、06 文档与项目根目录 README.md 阅读。下文中的测试数量、未接入密钥和未实现鉴权等描述仅适用于当时。

项目已创建于 `/Users/susu/Desktop/research-workbench`，本地工作台运行于 http://localhost:3000，API 文档位于 http://localhost:8000/docs。

## 启动方式

双击项目中的 `启动工作台.command`，或在终端运行：

```bash
cd /Users/susu/Desktop/research-workbench
bash scripts/dev.sh
```

服务正在运行时无需重复启动。停止服务可在启动终端按 Ctrl+C。开发服务使用 Webpack 轮询，避开本机 Turbopack 文件监听限制。

## 已交付

- Next.js 与 React 前端、FastAPI 后端，以及真实 LangGraph 研究状态图。
- 项目、背景资料、研究记录与可编辑计划。
- 并行 Researcher、对象级证据缺口重试、结构化综合输出。
- SQLite 业务与检查点持久化，暂停、恢复、取消、重启后恢复。
- 原文证据侧栏、报告与对比表共享结论、CSV 与 Markdown 导出。
- SSE 事件重放与执行轨迹。
- 父任务证据复用的增量研究。
- TXT、Markdown、CSV 上传与本地关键词检索。
- 模型兼容接口和 Tavily 适配器，包含搜索、正文读取与资料检索工具调用。
- 需求、系统设计与开发计划已复制到项目 `docs/`。

## 验证结果

后端 12 项测试通过，覆盖计划修改、预算与版本冲突、非法引用、暂停恢复、检查点重启、增量复用、取消、上传、导出、事件重放、来源限制与搜索摘要不充当证据。

前端 TypeScript 检查和生产构建通过。应用内浏览器完成提交问题、修改计划、执行、报告交付、对比表和点击原文证据的人工验收。

Playwright 浏览器自动化脚本已编写，但本机沙箱禁止 Chromium 注册所需的进程权限，浏览器启动失败，因此不将这两项自动化测试记为通过。

## 当前边界

默认演示模式使用明确标注的模拟资料，不是实际联网调研。真实服务需要在项目 `.env` 中配置模型标识、模型服务密钥、服务地址和 Tavily 密钥；本次没有使用真实密钥，也没有执行付费调用。

当前使用本地单进程调度与 SQLite。PostgreSQL、pgvector、Redis、Celery、独立 MCP 进程、Skills、Langfuse、成果编辑、语义冲突验证及演示稿尚未实现，均保留在后续阶段。PDF 解析与项目资料混合检索已在后续增量落地。

当前引用检查验证结构与证据 ID，不保证语义支持正确。调用预算为次数上限，尚未实现货币成本预留。应用仅面向本机个人使用，部署公网前需要加入鉴权与多用户隔离。

## 冲突与时效（后续增量）

`gap_check` 现已评估未覆盖对象、来源冲突和证据时效。证据表增加可空的 `published_at`，与 `fetched_at` 分开；未知发布日期保持为空，不会用获取时间填补。结论与证据通过 `claim_evidence.relation`（`support` / `conflict`）关联。演示模式在「定价 / 价格 / 收费」维度投放互斥模拟口径；计划勾选关注时效后，过时或未标注日期的格子可触发一轮补充检索。报告、对比表、证据侧栏和 SSE `coverage.checked` 事件都会带上冲突与时效，前端无需从正文反推。

## PDF 与混合检索（后续增量）

上传支持 TXT / Markdown / CSV / PDF。解析按页或段落分块，写入 FTS5 与本地哈希向量；状态为 `processing` / `ready` / `failed`，失败会保留错误信息，不会静默丢文件。检索先按 `project_id` 与当前 `embedding_model`/`index_version` 过滤，再各召回约 20 条，按内容哈希去重，RRF 融合后词法重排到 6–10 条。换模型会重建向量，不混用空间。`search_project` 与项目工具面 `read_evidence` 返回带 `page:` 定位的原文；证据侧栏展示页码。演示模式无密钥时使用 `local-hash-v1`。

项目完整操作说明位于桌面项目根目录 `README.md`。Git 初始化受到当前环境对 `.git` 的写入限制，本次未创建 Git 仓库。

## 后续开发顺序

先用用户配置的真实服务验证模型工具循环，再建设 PDF 解析与混合检索；之后接入 MCP、Skills、Langfuse 与评测集，并迁移到生产任务队列。各阶段都保留本地演示模式和固定样例回归。
