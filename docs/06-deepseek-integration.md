# DeepSeek 接入说明

调研日期：2026-10-07。项目位于 `/Users/susu/Desktop/research-workbench`。

## 选择

采用官方 Chat Completions 接口，模型为 `deepseek-flash`，服务地址 `https://api.deepseek.com`。官方模型页面将其对应到 DeepSeek-V4.1-Flash，支持工具调用和 JSON 输出，定价低于 Pro。适合作品开发与功能验证，后续可通过修改模型名称对比 `deepseek-v4-pro`。

来源：[官方模型与价格](https://api-docs.deepseek.com/quick_start/pricing/)、[Chat Completions](https://api-docs.deepseek.com/api/create-chat-completion/)、[思考模式](https://api-docs.deepseek.com/guides/thinking_mode/)、[JSON 输出](https://api-docs.deepseek.com/guides/json_mode/)。

## 已实现

- 普通聊天与研究 Agent 共用模型请求适配器；兼容原有 OpenAI 格式服务。
- DeepSeek 专属参数：思考模式和推理力度，默认显式关闭思考模式。
- 请求限制：输出上限 8192 Token、超时 120 秒；不自动重试收费请求。
- 研究工具循环完整保留并回传 `reasoning_content`，支持开启思考模式后继续调用工具。
- 规划与综合使用 JSON Output 和 Pydantic 校验；空回复、截断回复拒绝交付。
- 401、402、429 等错误转换为本地提示，不将供应商响应正文或密钥写入提示。
- 健康接口只展示模型、供应商和配置就绪状态，不返回密钥。
- 本地配置 `.env` 已保存模型密钥，文件权限为 600，已在 Git 忽略规则内。文档不包含密钥。

## 开通与启用

在 [DeepSeek API 平台](https://platform.deepseek.com/)自行创建 API Key，并按需配置平台余额。在本机项目 `.env` 文件填写 `LLM_API_KEY`。不需要把密钥发送到聊天中。

配置预设如下：

```dotenv
LLM_PROVIDER=deepseek
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
LLM_API_KEY=
CHAT_MODE=auto
DEEPSEEK_THINKING=disabled
DEEPSEEK_REASONING_EFFORT=high
LLM_MAX_TOKENS=8192
LLM_TIMEOUT_SECONDS=120
RESEARCH_MODE=demo
TAVILY_API_KEY=
```

填写模型密钥并重启后，普通会话自动切换到真实 DeepSeek 回复。若要真实联网研究，还需要填写 `TAVILY_API_KEY` 并将 `RESEARCH_MODE` 改为 `live`。DeepSeek 模型接口不会代替联网搜索工具。

可把 `DEEPSEEK_THINKING` 改为 `enabled` 开启思考模式。输出上限包括模型推理消耗；如果出现输出截断，需调整预算或缩小任务范围。

## 验证命令

在项目目录执行下面的命令，仅检查认证和模型列表：

```bash
apps/api/.venv/bin/python scripts/check_model.py
```

增加 `--chat` 会执行一次简短的生成请求，按供应商规则计费：

```bash
apps/api/.venv/bin/python scripts/check_model.py --chat
```

配置后通过双击 `启动工作台.command` 或 `bash scripts/dev.sh` 重启服务。

## 本次验收

28 项后端测试通过，包含 DeepSeek 请求契约、JSON 模式、工具调用、思考字段回传、缓存复用、错误脱敏与账号隔离。前端类型检查与生产构建通过。

2026-10-08 已完成真实供应商认证与简短生成测试，使用 deepseek-flash，测试消耗 12 Token。重启后的健康接口确认普通会话为 live，模型配置就绪。2026-10-08 已配置 Tavily，测试搜索返回 HTTP 200 和一条结果；联网研究模式已启用。28 项自动测试使用模拟 HTTP 响应；短请求连通成功不代表完整研究质量已经验收。普通会话现支持 POST SSE 流式响应，保存增量内容并在中断时保留部分回复。新增流式测试覆盖 UTF-8 跨包、增量事件、Token 统计、幂等重放及断流持久化；后端共 31 项测试通过。真实 DeepSeek 流式短请求验证收到 14 个文字分段，消耗 32 Token。
