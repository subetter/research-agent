# Langfuse 追踪（可选）

默认关闭。没有 `LANGFUSE_PUBLIC_KEY`、`LANGFUSE_SECRET_KEY`、`LANGFUSE_HOST` 时，工作台与演示模式的行为不变，也不会访问任何观测服务。追踪失败不会让研究或会话失败。

## 一条研究任务在 Langfuse 里长什么样

- **一条 trace**：`id` 等于业务 `run_id`，也等于 LangGraph 主图 `thread_id` 和 `run_events` 上的 run。
- **图节点 span**：`clarify`、`plan`、`approve`、`research`、`gap_check`、`synthesize`、`verify`、`render`。
- **对象子图 span**：`researcher:{对象}`，下面还有 `investigate`。
- **模型 generation**：每次 `llm.complete` / `llm.stream_complete` 记模型名、prompt、输出、token、时延；推理 token 计入 `reasoning_tokens`，**不保存思维链正文**。
- **工具 span**：`search_web`、`read_source`、`search_project` 记录输入和截断后的输出，不含网页全文或 Tavily 密钥。
- **分数 / 元数据**：覆盖率、核验统计（`fully` / `partial` / `reference_checked` 等）挂在同一条 trace 上。综合阶段会写入 `synthesis.included_evidence_ids` 以及每格入报告条数，与 `synthesis.context` 事件一致。

普通会话用会话 id 另开一条 `chat-conversation` trace，每轮是 `chat.turn`。

## 本地自托管（可选）

工作台本身不依赖 Docker。若要在本机看界面：

```bash
docker compose -f infra/langfuse/docker-compose.yml up -d
```

浏览器打开 http://localhost:3001 ，注册后在设置里复制公钥与密钥。写入项目根目录 `.env`（不要提交真实密钥）：

```dotenv
LANGFUSE_PUBLIC_KEY=pk-lf-...
LANGFUSE_SECRET_KEY=sk-lf-...
LANGFUSE_HOST=http://localhost:3001
```

重启 API。任务页在追踪开启时会出现「查看轨迹」，链到 `{LANGFUSE_HOST}/trace/{run_id}`。

`LANGFUSE_REDACT` 可追加逗号分隔的额外敏感串。出站前会去掉 `Authorization`、Cookie、Tavily `api_key` 以及已配置的密钥值。

## 关闭

清空上述三个变量或不要写它们。不要启动 compose 也可以正常做研究。
