# 离线评测（P0-3）

仓库根目录 `evaluations/`，不在 `apps/api/app` 内。不接 Langfuse / LangSmith / Postgres。不访问真实 Tavily 或模型 HTTP。

## 覆盖什么

10 道固定题，三类：

- 竞品比较：`comp-chatgpt-gemini`、`comp-claude-gemini`、`comp-cursor-copilot`、`comp-atlas-beacon`
- 缺失信息：`miss-inkbot-price`、`miss-empty-raw`、`miss-third-vendor`
- 冲突来源：`conf-price`、`conf-deploy`、`conf-date`

`catalog.py` 是题面与回放策略的源；`build_fixtures.py` 写成 `fixtures/<id>/{task,tavily,model,policies}.json`。模型与搜索响应钉在文件里，分数不随网络变化。综合/核验的 evidence id 不能预写（运行时生成），由 harness 按对象×维度绑定已入库原文。

## 两个系统、同一预算

- **工作台（被测）**：完整研究图，含计划中断、`read_source` 原文、对象×维度缺口、综合、核验。
- **基线 A**：一次搜索 + 一次综合，搜索摘要入库，不跑研究图，不做 `read_source`。

两边 `max_model_calls=80`、`report_reserved_calls=4`、`max_search_calls=24`。每题重复 3 次，均值包含全部重复，不取最好一次；失败单独列出。

评测强制 `RESEARCH_MODE=live` + 占位密钥 + HTTP 回放。演示路径仍只标 `reference_checked`，评分器对 demo 的语义支持率记为 n/a，不把规则标记当成语义核验。Live 缺配置不会落到 demo。

## 指标

| 指标 | 计算 | 算不出时 |
| --- | --- | --- |
| 维度覆盖率 | `coverage.covered / coverage.total` | 无 coverage 则 n/a |
| 引用完整率 | 带合法 evidence id 的 fact 数 / fact 数 | 没有 fact 则 n/a |
| 引用支持率 | 已引用结论中 `verification` 为 fully/partial 的比例 | demo 为 n/a |
| 模型/搜索次数与 Token | `usage` 表按 kind 汇总 | 无 usage 则 n/a |

引用支持率来自核验节点标签，不是人工复标，也不是独立语义模型重打分。

## 怎么跑

需要 `apps/api` 的依赖（与后端 pytest 同一环境）。无需 API 密钥：

```bash
cd evaluations
../apps/api/.venv/bin/python build_fixtures.py
../apps/api/.venv/bin/python run.py --write-fixtures --out results/latest
../apps/api/.venv/bin/python score.py results/latest
```

`run.py` 写出每题每系统每次重复的 artifact JSON，打印结果表，并更新本目录 `RESULTS.md`。`results/work/` 是临时 SQLite，不入库。

## 联网评测（可选）

默认仍是离线回放。`--live` 才走仓库根目录 `.env` 的 `LLM_API_KEY` / `LLM_MODEL` / `TAVILY_API_KEY`。缺任一密钥直接拒绝启动，不回落演示，也不打印密钥。

题面与离线集相同，但搜索和模型是真请求，不使用夹具里的原文、回放主张或 `page_subjects`。工作台与基线 A 同一预算。联网默认每题 1 次。未指定 `--tasks` 时冒烟三题：`comp-chatgpt-gemini`（竞品）、`miss-inkbot-price`（缺失）、`conf-price`（冲突）。

费用按配置里的 DeepSeek + Tavily 估价表计算，带版本与日期，**不是账单**。会打印本批费用，并按本批题均外推 10 题。结果写到 `evaluations/results/live-<UTC时间戳>/`（已 gitignore），含 `samples.csv`（最多 30 条已引用结论，空着 `human_label` 供人工标注）。填完后：

```bash
../apps/api/.venv/bin/python agreement.py results/live-<时间戳>/samples.csv
```

联网开始前会对 plan / 研究工具循环 / synthesize / verify 四种请求形状各打一次极小预检，任一 400 立刻中止。每题默认 token 顶 80000（可用 `EVAL_MAX_TOKENS_PER_TASK` 改），触顶后转入报告，不继续烧研究调用。

本机三题冒烟（在仓库根目录，`.env` 已填密钥）：

```bash
apps/api/.venv/bin/python evaluations/run.py --live --tasks comp-chatgpt-gemini miss-inkbot-price conf-price --repeats 1
```

CI 与默认 `pytest` 不会发真实请求。

## 不在范围内

子图检查点、Langfuse / LangSmith、MCP、Postgres、24 题消融。联网人工标注只提供抽样表与一致率脚本，不自动打分。不改计划中断 / 覆盖 / 核验行为，harness 只调用现有图。
