---
name: research_report
version: "1"
title: 综合研究报告
when_to_use: 开放主题的深挖研究，或问题无法明确归为竞品对照时使用。规划成子问题树，而不是对象×维度对照表。
required_dimensions:
allowed_tools: search_web,read_source,search_project,cite_project
output_schema: {"type":"object","required":["summary","sections"],"properties":{"summary":{"type":"string"},"sections":{"type":"array"},"open_questions":{"type":"array"},"claims":{"type":"array"}}}
---

# 综合研究报告

综合报告技能正文：事实与推断分开，缺口标为未确认。把主题拆成可编辑的子问题树，多轮检索并跟进未知点。

## 方法

1. 先判断模式：多个对象要对照时用 grid；否则用 general，输出 subquestions 树。
2. 每个子问题阅读正文；搜索摘要不能当证据。根据仍未知的要点提出后续查询。
3. 项目资料先出候选，选用后才成为证据。
4. 预算不足时基于已有证据交付，不要编造来源。覆盖按子问题计。

## 输出

先写摘要与章节（正文可用 [[evidence_id]] 引用），再列未解问题与来源。每条事实附 evidence_ids。
