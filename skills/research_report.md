---
name: research_report
version: "1"
title: 综合研究报告
when_to_use: 目标是一份有引用的综合报告，或问题无法明确归为竞品对照或行业格局时使用。
required_dimensions: 产品形态,研究流程,交付方式
allowed_tools: search_web,read_source,search_project,cite_project
output_schema: {"type":"object","required":["summary","claims"],"properties":{"summary":{"type":"string"},"claims":{"type":"array","items":{"type":"object","required":["subject","dimension","text","kind"]}}}}
---

# 综合研究报告

综合报告技能正文：事实与推断分开，缺口标为未确认。

## 方法

1. 按计划对象与维度收集一手来源并阅读正文。
2. 事实必须能指回原文摘录；分析不得冒充事实。
3. 项目资料先出候选，选用后才成为证据。
4. 预算不足时基于已有证据交付，不要编造来源。

## 输出

先写摘要，再按对象与维度给出主张，每条事实附 evidence_ids。
