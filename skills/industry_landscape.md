---
name: industry_landscape
version: "1"
title: 行业格局
when_to_use: 梳理一个行业或品类的玩家、价值链与进入壁垒。默认用开放深挖（general）拆子问题；只有用户点名多个对照对象时才用 grid。
required_dimensions: 产品定位,目标用户,核心能力
allowed_tools: search_web,read_source,search_project
output_schema: {"type":"object","required":["summary","claims"],"properties":{"summary":{"type":"string"},"claims":{"type":"array"}}}
---

# 行业格局

行业格局技能正文：先画价值链再落到玩家与进入壁垒。

## 方法

1. 先描述行业边界、主要环节和谁在付费，再点名玩家。
2. 用原始公告、监管或行业报告支撑格局判断；转载需标明。
3. 只使用已授权的工具。本技能不选用项目段落入证，检索命中仅作背景。
4. 不要把单个产品手册写成全行业结论。

## 输出

摘要说明格局，主张按对象×维度展开；缺口标未确认。
