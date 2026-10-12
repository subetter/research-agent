# Research Workbench

一个面向竞品和行业研究的 Agent 工程作品。用户在项目中发起研究，修改计划，查看并行研究过程，通过原文证据检查结论，并导出报告与对比表。

## 启动

在项目目录双击 `启动工作台.command`，或执行：

```bash
bash scripts/dev.sh
```

工作台位于 http://localhost:3000，API 文档位于 http://localhost:8000/docs。启动器默认只监听本机，Ctrl+C 停止。当前支持多账号与管理员审阅，采用单进程本地运行。公网部署还需要 HTTPS、生产会话配置与独立调度。

依赖要求 Node.js 20.9 以上、uv 与 Python 3.12。已安装的开发依赖保留在项目内；新环境首次运行需访问包仓库。前端 API 请求由 Next.js 转发至本地 FastAPI。

## 账号与会话

首次注册的账号自动成为管理员，并接收升级前的本地项目与研究记录。后续注册账号默认为普通用户，没有默认账号或默认密码。

普通会话支持新增、切换、历史持久化和多轮上下文，回复标明模型或演示状态。普通会话采用 POST SSE 流式生成，文字到达即显示。浏览器重连会读取已保存的消息，服务重启会把中断的回复标记为失败。

密码使用加盐 scrypt 哈希，Session Cookie 为 HttpOnly / SameSite=Strict，有效期七天，数据库只保存 Token 哈希；退出登录立即撤销会话。项目、任务、资料、证据、执行轨迹与导出均校验归属。

管理员页提供账号统计、账号筛选、标题搜索、分页和会话及研究详情的只读审阅。查看记录列表与详情会写入访问审计。管理员不能通过普通接口修改其他账号的数据。

默认普通聊天也使用标明的演示回复。普通会话的 Live 模式仅需模型、兼容接口和模型密钥，不需要 Tavily；深度研究还需要 Tavily。尚未使用真实供应商密钥验证模型回复，多轮上下文契约通过模拟 HTTP 响应测试。

## 已实现

- Next.js 与 React 研究工作台，中文界面、响应式布局。
- 项目创建、背景记录、研究历史。
- 真实 LangGraph 状态图：规划、人工确认、并行研究、缺口重试、综合、引用结构校验、交付。
- SQLite 业务持久化与独立 LangGraph 检查点。
- 并行 Researcher、调用次数预算、结构化输入输出。
- 任务暂停、恢复、取消与服务器重启后手动恢复。
- 证据去重与稳定 ID，报告点击查看原文。
- 持久化事件、SSE 重放与执行轨迹。
- 增量研究：相同维度的父任务证据复用；复用是否成立看 **发布时间** 与计划时效，而不是获取时间。
- 来源冲突：同一对象×维度下互斥口径会写入 `claim_evidence.relation=conflict`，报告与对比表并列双方，不静默合并。
- 证据时效：`published_at` 与 `fetched_at` 分开保存；发布日期未知时留空。缺口检查可按计划把过时或未标注日期当作缺口并补充检索。
- UTF-8 TXT、Markdown、CSV 与 PDF 上传；解析后按页/段分块，状态为处理中 / 已就绪 / 失败。
- 项目资料混合检索：SQLite FTS5 + 本地哈希向量，内容哈希去重、RRF 融合、词法重排；换嵌入模型会重建索引，不混用向量空间。
- Markdown 报告与 CSV 导出。
- 模型兼容接口与 Tavily 联网适配器，模型工具调用包含搜索、正文读取与项目检索。

## 演示模式与联网模式

### DeepSeek 预设

当前本地 `.env` 已预设 `LLM_PROVIDER=deepseek`、`LLM_BASE_URL=https://api.deepseek.com` 与 `LLM_MODEL=deepseek-flash`，密钥保存在本机 `.env` 中。更换密钥后请重启；`CHAT_MODE=auto` 会让普通会话自动使用真实模型。联网研究还需 `TAVILY_API_KEY` 和 `RESEARCH_MODE=live`。

默认 `DEEPSEEK_THINKING=disabled`。开启思考模式时，研究工具循环保留并回传推理字段。请求输出上限为 8192 Token，超时 120 秒，可在本地配置中调整。普通聊天与研究任务共享 DeepSeek 适配器。

在项目目录执行 `apps/api/.venv/bin/python scripts/check_model.py` 检查认证与模型列表；追加 `--chat` 执行一次按 Token 计费的简短生成请求。2026-10-08 已通过真实认证和简短生成测试，消耗 12 Token；本地 Tavily 认证与测试搜索已通过；真实研究模式已配置。完整说明见 `docs/06-deepseek-integration.md`。

默认 `RESEARCH_MODE=demo`，无需 API 密钥。模拟资料均明确标记，不包含真实产品事实。这一模式验证流程、引用关联、增量复用与任务恢复，不代表模型研究质量。

要使用真实服务，复制 `.env.example` 为 `.env`，填写：

```dotenv
RESEARCH_MODE=live
LLM_API_KEY=你的模型服务密钥
LLM_BASE_URL=https://你的模型服务/v1
LLM_MODEL=支持工具调用和JSON输出的模型标识
TAVILY_API_KEY=你的Tavily密钥
```

模型适配器使用 Chat Completions 兼容接口，采用 Tool Calling、JSON 模式与 Pydantic 结果校验；不同供应商的兼容性需单独验证。联网模式缺少配置会拒绝启动研究，不会静默切换为模拟。仅把 Tavily 成功取得的正文作为网页证据，搜索摘要不计为证据。

当前只控制调用次数，记录 Token，不宣称精确费用预算。外部调用状态不确定时保留占用；显式重试可能受预算与未知调用状态影响。结构校验不等于事实验证，需要人工检查原文语义是否支持结论。

## 试用 PDF 与混合检索

1. `bash scripts/dev.sh`，打开 http://localhost:3000。
2. 进入「项目资料」，上传一份 PDF 或 Markdown。卡片会显示处理中 / 已就绪 / 失败。
3. 发起研究并让范围覆盖资料中的对象或维度。演示模式会把已就绪资料作为带页码的证据一并引用。
4. 打开证据侧栏，确认「页码」与 `page:N;paragraph:M` 定位。失败的解析会留在资料库并给出原因，不会进入检索。

默认嵌入为 `local-hash-v1`，无需密钥。若配置 `EMBEDDING_PROVIDER=openai_compatible` 与 `EMBEDDING_API_KEY`，换模型后下一次检索会重建该项目向量。

## 测试

```bash
cd apps/api
.venv/bin/python -m pytest -q
```

```bash
cd apps/web
npm run typecheck
npm run build
```

浏览器测试需要本地服务运行与 Playwright Chromium：

```bash
cd apps/web
PLAYWRIGHT_BROWSERS_PATH=../../.cache/browsers npx playwright install chromium
PLAYWRIGHT_BROWSERS_PATH=../../.cache/browsers npx playwright test
```

## 工程边界

首版无 Docker 前置要求，采用单进程本地调度。只读研究工具有结果缓存，证据写入幂等；暂未实现多 Worker 租约 fencing、事务 Outbox、生产鉴权与任务消息队列。运行图可以在节点边界恢复；外部请求无法保证只计费一次。项目资料检索留在 SQLite（FTS5 + 本地向量），不是 PostgreSQL/pgvector。网页来源仍无完整快照体系。

缺口检查覆盖对象级原文、启发式来源冲突，以及计划勾选「关注证据时效」时的过时/未标注日期。冲突检测依据价格数字与极性对立，不是完整语义仲裁。Live 研究节点包含模型工具循环，但联网供应商调用需要用户配置密钥后做实际验证。并行调用和本地读写边界已纳入测试。

## 下一阶段

1. PostgreSQL / pgvector 与生产对象存储（当前 PDF 与混合检索已在 SQLite 落地）。
2. 独立 MCP 进程与按需加载 Skills（项目工具 search_project / read_evidence 已作为会话注入的工具面提供）。
3. Redis 与 Celery、任务 Outbox、租约与更完整幂等账本。
4. Langfuse、固定评测集、单 Agent 与多 Agent 对照。
5. 成果版本编辑、更完整的语义冲突验证、演示稿与隔离代码沙箱。

设计方案位于 `docs/`，阶段目标与当前实现范围不同。真实数据、API 密钥、运行文件和缓存已从 Git 忽略。
