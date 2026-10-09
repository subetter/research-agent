---
name: competitor_analysis
version: "1"
title: 竞品分析
when_to_use: 比较两个及以上产品或厂商在同一组维度上的差异，输出对照表。
required_dimensions: 产品形态,定价,部署方式
allowed_tools: search_web,read_source,search_project,cite_project
output_schema: {"type":"object","required":["subjects","dimensions","claims"],"properties":{"subjects":{"type":"array"},"dimensions":{"type":"array"},"claims":{"type":"array"}}}
---

# 竞品分析

竞品分析技能正文：每个对象同一维度必须能对照。

## 方法

1. 先锁定可比较的对象与同一组验收维度，不要中途更换口径。
2. 每个对象先找官方或一手来源，再读正文；搜索摘要不能当证据。
3. 同一维度写成可并列的短结论，缺失就标未确认，禁止用其他对象的事实填补。
4. 项目资料需先检索候选再选用；未标记可引用的资料只能支撑分析。

## 输出

覆盖计划中的全部对象×维度。事实必须带 evidence_ids；推断标为 analysis。
