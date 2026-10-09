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

默认普通聊天也使用标明的演示回复。普通会话的 Live 模式仅需模型、兼容接口和模型密钥，不需要 Tavily；深度研究还需要 Tavily。2026-10-08 已用真实 DeepSeek 与 Tavily 密钥做过短请求连通（生成 12 / 32 Token，Tavily HTTP 200）；pytest 仍使用模拟 HTTP，没有完整联网研究质量评测。多轮上下文契约通过模拟 HTTP 响应测试。

## 已实现

- Next.js 与 React 研究工作台，中文界面、响应式布局。
- 项目创建、背景记录、研究历史。
- 真实 LangGraph 状态图：范围不足时先澄清一次、规划、人工确认、并行研究、对象×维度缺口检查与定向补充、综合、引用结构校验、原文支持性核验、交付。
- Skills 按需加载：计划节点先看 `skills/` 目录摘要，选定后再注入正文；计划与事件记录技能名和版本，计划卡展示研究方法。
- 研究进度经 SSE 推送（对象、工具、截断查询/URL、刚填上的覆盖格子），轨迹按对象归类；覆盖矩阵随格子填上更新。聊天仍按 Token 流式，不推送思维链。
- 项目资料检索只返回候选，选用后才入证；未标记为可引用事实的上传资料只能支撑分析。
- SQLite 业务持久化与独立 LangGraph 检查点；每个对象的研究员作为子图，使用独立检查点命名空间，崩溃后跳过已完成对象。
- 并行 Researcher、调用次数预算、结构化输入输出。
- 任务暂停、恢复、取消与服务器重启后手动恢复。
- 证据带维度标签、canonical URL / 正文哈希去重与稳定 ID，报告点击查看原文。
- 持久化事件、SSE 重放与执行轨迹。
- 增量研究：相同维度的父任务证据复用；联网证据初始有效期为一天。
- UTF-8 TXT、Markdown、CSV 上传与本地关键词检索。
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

当前只控制调用次数，记录 Token，不宣称精确费用预算。外部调用状态不确定时保留占用；显式重试可能受预算与未知调用状态影响。结构校验拦住幻觉 ID；联网模式会再对照入库原文判断是否支持主张，不被支持的事实降为待确认。这仍不是事实验证，不能保证结论为真。演示模式只按规则标记引用是否存在，不做语义判断。

## 测试

```bash
cd apps/api
.venv/bin/python -m pytest -q
```

离线评测（无需 API 密钥，Tavily/模型 HTTP 钉在 `evaluations/fixtures/`）：

```bash
cd evaluations
../apps/api/.venv/bin/python run.py --write-fixtures --out results/latest
```

说明与最近一次数字见 `evaluations/README.md`、`evaluations/RESULTS.md`。评分脚本只读导出的 artifact JSON。演示模式的语义支持率记为 n/a。

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

首版无 Docker 前置要求，采用单进程本地调度。前端是 npm lockfile 与 `globals.css`，没有 Tailwind，也没有 Docker Compose。只读研究工具有结果缓存，证据写入幂等；暂未实现多 Worker 租约 fencing、事务 Outbox、生产鉴权与任务消息队列。运行图可以在节点边界恢复；外部请求无法保证只计费一次。资料目前为原文片段存储，没有向量索引、完整网页快照与内容版本体系。

缺口检查按对象×维度格子判断是否已有直接原文；补充轮只针对未覆盖格子做定向检索，并遵守每对象搜索配额与 `max_gap_rounds`。联网模式在综合之后对照入库原文做支持性核验，演示模式只标记引用是否存在。`evaluations/` 提供 10 题固定快照离线评测（竞品 / 缺失 / 冲突），对照单次搜索基线 A，重复 3 次取全部均值。这不是来源冲突自动识别，也不是 24 题消融或 Langfuse。并行调用和本地读写边界已纳入测试。

## 下一阶段

1. PDF 解析、PostgreSQL 与 pgvector 混合检索、重排。
2. MCP 项目工具 Server 与按需加载 Skills。
3. Redis 与 Celery、任务 Outbox、租约与更完整幂等账本。
4. Langfuse / LangSmith、24 题消融、人工标注对、实时联网评测。
5. 成果版本编辑、冲突检测、演示稿与隔离代码沙箱。

设计方案位于 `docs/`，阶段目标与当前实现范围不同。真实数据、API 密钥、运行文件和缓存已从 Git 忽略。
