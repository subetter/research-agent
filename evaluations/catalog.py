"""Offline eval task definitions. build_fixtures.py freezes these into JSON."""

from __future__ import annotations

import json


def tool_call(call_id: str, name: str, **args) -> dict:
    return {
        "role": "assistant",
        "content": None,
        "tool_calls": [{"id": call_id, "type": "function", "function": {"name": name, "arguments": json.dumps(args, ensure_ascii=False)}}],
    }


def assistant(text: str) -> dict:
    return {"role": "assistant", "content": text}


def page(url: str, title: str, snippet: str, raw: str) -> dict:
    return {"title": title, "url": url, "snippet": snippet, "raw_content": raw}


def result_pack(*pages: dict) -> dict:
    return {"results": list(pages)}


def research_loop(subject: str, query: str, urls: list[str], empty_round: bool = False) -> dict:
    keys = {
        f"research-model:{subject}:0:0": tool_call(f"{subject}-s0", "search_web", query=query),
    }
    turn = 1
    for index, url in enumerate(urls):
        keys[f"research-model:{subject}:0:{turn}"] = tool_call(f"{subject}-r{index}", "read_source", url=url)
        turn += 1
    keys[f"research-model:{subject}:0:{turn}"] = assistant("调查完成")
    if empty_round:
        keys[f"research-model:{subject}:1:0"] = tool_call(f"{subject}-s1", "search_web", query=f"{subject} 补充 缺失维度")
        keys[f"research-model:{subject}:1:1"] = assistant("未找到更多原文")
    return keys


TASKS = [
    {
        "id": "comp-chatgpt-gemini",
        "family": "competitor",
        "question": "比较 ChatGPT 与 Gemini 的产品形态和定价",
        "subjects": ["ChatGPT", "Gemini"],
        "dimensions": ["产品形态", "定价"],
        "baseline_query": "ChatGPT Gemini 产品形态 定价",
        "pages": {
            "https://example.com/chatgpt": page(
                "https://example.com/chatgpt",
                "ChatGPT 产品说明",
                "ChatGPT 是对话助手。",
                "产品形态：ChatGPT 是面向个人的对话式研究与写作助手，在浏览器中完成问答。\n\n定价：ChatGPT Plus 为每月 20 美元，另有免费档。",
            ),
            "https://example.com/gemini": page(
                "https://example.com/gemini",
                "Gemini 产品说明",
                "Gemini 是多模态助手。",
                "产品形态：Gemini 是谷歌的多模态助手，可在搜索与文档中调用。\n\n定价：Gemini Advanced 按 Google One 高级套餐计费。",
            ),
        },
        "queries": {
            "ChatGPT 产品形态 定价 官方": ["https://example.com/chatgpt"],
            "Gemini 产品形态 定价 官方": ["https://example.com/gemini"],
            "ChatGPT Gemini 产品形态 定价": ["https://example.com/chatgpt", "https://example.com/gemini"],
            "ChatGPT 补充 缺失维度": ["https://example.com/chatgpt"],
            "Gemini 补充 缺失维度": ["https://example.com/gemini"],
        },
        "loops": {
            "ChatGPT": ("ChatGPT 产品形态 定价 官方", ["https://example.com/chatgpt"], False),
            "Gemini": ("Gemini 产品形态 定价 官方", ["https://example.com/gemini"], False),
        },
        "claims": [
            {"subject": "ChatGPT", "dimension": "产品形态", "kind": "fact", "text": "ChatGPT 是面向个人的对话式助手。", "verification": "fully"},
            {"subject": "ChatGPT", "dimension": "定价", "kind": "fact", "text": "ChatGPT Plus 为每月 20 美元。", "verification": "fully"},
            {"subject": "Gemini", "dimension": "产品形态", "kind": "fact", "text": "Gemini 是谷歌的多模态助手。", "verification": "fully"},
            {"subject": "Gemini", "dimension": "定价", "kind": "fact", "text": "Gemini Advanced 随 Google One 高级套餐提供。", "verification": "fully"},
        ],
        "baseline_claims": [
            {"subject": "ChatGPT", "dimension": "产品形态", "kind": "fact", "text": "ChatGPT 是对话助手。", "verification": "partial"},
            {"subject": "ChatGPT", "dimension": "定价", "kind": "fact", "text": "ChatGPT 定价未在摘要中给出具体数字。", "verification": "unrelated"},
            {"subject": "Gemini", "dimension": "产品形态", "kind": "fact", "text": "Gemini 是多模态助手。", "verification": "partial"},
            {"subject": "Gemini", "dimension": "定价", "kind": "unknown", "text": "摘要未给出 Gemini 价格。", "verification": "unrelated"},
        ],
    },
    {
        "id": "comp-claude-gemini",
        "family": "competitor",
        "question": "比较 Claude 与 Gemini 的研究流程和交付方式",
        "subjects": ["Claude", "Gemini"],
        "dimensions": ["研究流程", "交付方式"],
        "baseline_query": "Claude Gemini 研究流程 交付方式",
        "pages": {
            "https://example.com/claude": page(
                "https://example.com/claude",
                "Claude 研究说明",
                "Claude 可写长报告。",
                "研究流程：Claude 先澄清问题，再检索并逐步写出带引用的草稿。\n\n交付方式：结果以对话消息和可下载 Markdown 交付。",
            ),
            "https://example.com/gemini-research": page(
                "https://example.com/gemini-research",
                "Gemini Deep Research",
                "Gemini 能做深度研究。",
                "研究流程：Gemini Deep Research 先给出可编辑计划，再后台浏览网页。\n\n交付方式：完成后生成带链接的研究报告。",
            ),
        },
        "queries": {
            "Claude 研究流程 交付方式 官方": ["https://example.com/claude"],
            "Gemini 研究流程 交付方式 官方": ["https://example.com/gemini-research"],
            "Claude Gemini 研究流程 交付方式": ["https://example.com/claude", "https://example.com/gemini-research"],
        },
        "loops": {
            "Claude": ("Claude 研究流程 交付方式 官方", ["https://example.com/claude"], False),
            "Gemini": ("Gemini 研究流程 交付方式 官方", ["https://example.com/gemini-research"], False),
        },
        "claims": [
            {"subject": "Claude", "dimension": "研究流程", "kind": "fact", "text": "Claude 先澄清再检索并写带引用草稿。", "verification": "fully"},
            {"subject": "Claude", "dimension": "交付方式", "kind": "fact", "text": "Claude 以对话和 Markdown 交付。", "verification": "fully"},
            {"subject": "Gemini", "dimension": "研究流程", "kind": "fact", "text": "Gemini Deep Research 先给出可编辑计划。", "verification": "fully"},
            {"subject": "Gemini", "dimension": "交付方式", "kind": "fact", "text": "Gemini 完成后生成带链接的研究报告。", "verification": "fully"},
        ],
        "baseline_claims": [
            {"subject": "Claude", "dimension": "研究流程", "kind": "fact", "text": "Claude 可写长报告。", "verification": "unrelated"},
            {"subject": "Claude", "dimension": "交付方式", "kind": "unknown", "text": "摘要未说明交付方式。", "verification": "unrelated"},
            {"subject": "Gemini", "dimension": "研究流程", "kind": "fact", "text": "Gemini 能做深度研究。", "verification": "partial"},
            {"subject": "Gemini", "dimension": "交付方式", "kind": "unknown", "text": "摘要未说明交付方式。", "verification": "unrelated"},
        ],
    },
    {
        "id": "comp-cursor-copilot",
        "family": "competitor",
        "question": "比较 Cursor 与 Copilot 的产品形态和目标用户",
        "subjects": ["Cursor", "Copilot"],
        "dimensions": ["产品形态", "目标用户"],
        "baseline_query": "Cursor Copilot 产品形态 目标用户",
        "pages": {
            "https://example.com/cursor": page(
                "https://example.com/cursor",
                "Cursor 介绍",
                "Cursor 是 AI 编辑器。",
                "产品形态：Cursor 是带智能补全与 Agent 的独立代码编辑器。\n\n目标用户：主要面向职业软件工程师和独立开发者。",
            ),
            "https://example.com/copilot": page(
                "https://example.com/copilot",
                "GitHub Copilot",
                "Copilot 是插件。",
                "产品形态：GitHub Copilot 作为 IDE 插件提供补全与聊天。\n\n目标用户：GitHub 上的开发者与企业团队。",
            ),
        },
        "queries": {
            "Cursor 产品形态 目标用户 官方": ["https://example.com/cursor"],
            "Copilot 产品形态 目标用户 官方": ["https://example.com/copilot"],
            "Cursor Copilot 产品形态 目标用户": ["https://example.com/cursor", "https://example.com/copilot"],
        },
        "loops": {
            "Cursor": ("Cursor 产品形态 目标用户 官方", ["https://example.com/cursor"], False),
            "Copilot": ("Copilot 产品形态 目标用户 官方", ["https://example.com/copilot"], False),
        },
        "claims": [
            {"subject": "Cursor", "dimension": "产品形态", "kind": "fact", "text": "Cursor 是独立的 AI 代码编辑器。", "verification": "fully"},
            {"subject": "Cursor", "dimension": "目标用户", "kind": "fact", "text": "Cursor 面向职业软件工程师。", "verification": "fully"},
            {"subject": "Copilot", "dimension": "产品形态", "kind": "fact", "text": "Copilot 以 IDE 插件形式提供。", "verification": "fully"},
            {"subject": "Copilot", "dimension": "目标用户", "kind": "fact", "text": "Copilot 面向 GitHub 开发者与企业团队。", "verification": "fully"},
        ],
        "baseline_claims": [
            {"subject": "Cursor", "dimension": "产品形态", "kind": "fact", "text": "Cursor 是 AI 编辑器。", "verification": "partial"},
            {"subject": "Cursor", "dimension": "目标用户", "kind": "unknown", "text": "摘要未写目标用户。", "verification": "unrelated"},
            {"subject": "Copilot", "dimension": "产品形态", "kind": "fact", "text": "Copilot 是插件。", "verification": "partial"},
            {"subject": "Copilot", "dimension": "目标用户", "kind": "unknown", "text": "摘要未写目标用户。", "verification": "unrelated"},
        ],
    },
    {
        "id": "comp-atlas-beacon",
        "family": "competitor",
        "question": "比较 Atlas 与 Beacon 的部署方式和定价",
        "subjects": ["Atlas", "Beacon"],
        "dimensions": ["部署方式", "定价"],
        "baseline_query": "Atlas Beacon 部署方式 定价",
        "pages": {
            "https://example.com/atlas": page(
                "https://example.com/atlas",
                "Atlas 文档",
                "Atlas 可私有化。",
                "部署方式：Atlas 支持云端与私有化部署，企业可在内网安装。\n\n定价：Atlas Team 为每席每月 40 美元。",
            ),
            "https://example.com/beacon": page(
                "https://example.com/beacon",
                "Beacon 文档",
                "Beacon 只提供云端。",
                "部署方式：Beacon 仅提供多租户云端，不开放私有化安装。\n\n定价：Beacon Pro 为每年 480 美元。",
            ),
        },
        "queries": {
            "Atlas 部署方式 定价 官方": ["https://example.com/atlas"],
            "Beacon 部署方式 定价 官方": ["https://example.com/beacon"],
            "Atlas Beacon 部署方式 定价": ["https://example.com/atlas", "https://example.com/beacon"],
        },
        "loops": {
            "Atlas": ("Atlas 部署方式 定价 官方", ["https://example.com/atlas"], False),
            "Beacon": ("Beacon 部署方式 定价 官方", ["https://example.com/beacon"], False),
        },
        "claims": [
            {"subject": "Atlas", "dimension": "部署方式", "kind": "fact", "text": "Atlas 支持云端与私有化部署。", "verification": "fully"},
            {"subject": "Atlas", "dimension": "定价", "kind": "fact", "text": "Atlas Team 为每席每月 40 美元。", "verification": "fully"},
            {"subject": "Beacon", "dimension": "部署方式", "kind": "fact", "text": "Beacon 仅提供多租户云端。", "verification": "fully"},
            {"subject": "Beacon", "dimension": "定价", "kind": "fact", "text": "Beacon Pro 为每年 480 美元。", "verification": "fully"},
        ],
        "baseline_claims": [
            {"subject": "Atlas", "dimension": "部署方式", "kind": "fact", "text": "Atlas 可私有化。", "verification": "partial"},
            {"subject": "Atlas", "dimension": "定价", "kind": "unknown", "text": "摘要没有价格。", "verification": "unrelated"},
            {"subject": "Beacon", "dimension": "部署方式", "kind": "fact", "text": "Beacon 只提供云端。", "verification": "partial"},
            {"subject": "Beacon", "dimension": "定价", "kind": "unknown", "text": "摘要没有价格。", "verification": "unrelated"},
        ],
    },
    {
        "id": "miss-inkbot-price",
        "family": "missing",
        "question": "比较 NovaWriter 与 InkBot 的产品形态和定价，标出缺失信息",
        "subjects": ["NovaWriter", "InkBot"],
        "dimensions": ["产品形态", "定价"],
        "baseline_query": "NovaWriter InkBot 产品形态 定价",
        "pages": {
            "https://example.com/novawriter": page(
                "https://example.com/novawriter",
                "NovaWriter",
                "NovaWriter 是写作工具。",
                "产品形态：NovaWriter 是浏览器写作工作台。\n\n定价：NovaWriter Pro 每月 12 美元。",
            ),
            "https://example.com/inkbot": page(
                "https://example.com/inkbot",
                "InkBot",
                "InkBot 能改写文章。",
                "产品形态：InkBot 是嵌入博客后台的改写插件。\n\n公司只公布了形态介绍，没有公开费用页。",
            ),
        },
        "queries": {
            "NovaWriter 产品形态 定价 官方": ["https://example.com/novawriter"],
            "InkBot 产品形态 定价 官方": ["https://example.com/inkbot"],
            "NovaWriter InkBot 产品形态 定价": ["https://example.com/novawriter", "https://example.com/inkbot"],
            "InkBot 补充 缺失维度": ["https://example.com/inkbot"],
        },
        "loops": {
            "NovaWriter": ("NovaWriter 产品形态 定价 官方", ["https://example.com/novawriter"], False),
            "InkBot": ("InkBot 产品形态 定价 官方", ["https://example.com/inkbot"], True),
        },
        "claims": [
            {"subject": "NovaWriter", "dimension": "产品形态", "kind": "fact", "text": "NovaWriter 是浏览器写作工作台。", "verification": "fully"},
            {"subject": "NovaWriter", "dimension": "定价", "kind": "fact", "text": "NovaWriter Pro 每月 12 美元。", "verification": "fully"},
            {"subject": "InkBot", "dimension": "产品形态", "kind": "fact", "text": "InkBot 是博客后台改写插件。", "verification": "fully"},
            {"subject": "InkBot", "dimension": "定价", "kind": "unknown", "text": "未找到 InkBot 公开定价。", "verification": "unconfirmed"},
        ],
        "baseline_claims": [
            {"subject": "NovaWriter", "dimension": "产品形态", "kind": "fact", "text": "NovaWriter 是写作工具。", "verification": "partial"},
            {"subject": "NovaWriter", "dimension": "定价", "kind": "fact", "text": "NovaWriter 看起来是免费的。", "verification": "unrelated"},
            {"subject": "InkBot", "dimension": "产品形态", "kind": "fact", "text": "InkBot 能改写文章。", "verification": "partial"},
            {"subject": "InkBot", "dimension": "定价", "kind": "fact", "text": "InkBot 定价与 NovaWriter 相同。", "verification": "unrelated"},
        ],
    },
    {
        "id": "miss-empty-raw",
        "family": "missing",
        "question": "调查 Helio 的产品形态和部署方式",
        "subjects": ["Helio"],
        "dimensions": ["产品形态", "部署方式"],
        "baseline_query": "Helio 产品形态 部署方式",
        "pages": {
            "https://example.com/helio": page(
                "https://example.com/helio",
                "Helio 简介",
                "Helio 是协作白板。",
                "产品形态：Helio 是团队协作白板，支持注释和模板。",
            ),
            "https://example.com/helio-deploy": page(
                "https://example.com/helio-deploy",
                "Helio 部署",
                "部署信息已失效。",
                "",
            ),
        },
        "queries": {
            "Helio 产品形态 官方": ["https://example.com/helio", "https://example.com/helio-deploy"],
            "Helio 部署方式 官方": ["https://example.com/helio-deploy"],
            "Helio 产品形态 部署方式": ["https://example.com/helio", "https://example.com/helio-deploy"],
            "Helio 补充 缺失维度": ["https://example.com/helio-deploy"],
        },
        "loops": {
            "Helio": ("Helio 产品形态 官方", ["https://example.com/helio", "https://example.com/helio-deploy"], True),
        },
        "claims": [
            {"subject": "Helio", "dimension": "产品形态", "kind": "fact", "text": "Helio 是团队协作白板。", "verification": "fully"},
            {"subject": "Helio", "dimension": "部署方式", "kind": "unknown", "text": "部署页没有可读正文。", "verification": "unconfirmed"},
        ],
        "baseline_claims": [
            {"subject": "Helio", "dimension": "产品形态", "kind": "fact", "text": "Helio 是协作白板。", "verification": "partial"},
            {"subject": "Helio", "dimension": "部署方式", "kind": "fact", "text": "Helio 支持任意私有化部署。", "verification": "unrelated"},
        ],
    },
    {
        "id": "miss-third-vendor",
        "family": "missing",
        "question": "比较 Atlas、Beacon 与 Quark 的产品形态",
        "subjects": ["Atlas", "Beacon", "Quark"],
        "dimensions": ["产品形态"],
        "baseline_query": "Atlas Beacon Quark 产品形态",
        "pages": {
            "https://example.com/atlas-form": page(
                "https://example.com/atlas-form",
                "Atlas 形态",
                "Atlas 是研究工作台。",
                "产品形态：Atlas 是面向分析师的研究工作台。",
            ),
            "https://example.com/beacon-form": page(
                "https://example.com/beacon-form",
                "Beacon 形态",
                "Beacon 是监控面板。",
                "产品形态：Beacon 是实时监控面板。",
            ),
        },
        "queries": {
            "Atlas 产品形态 官方": ["https://example.com/atlas-form"],
            "Beacon 产品形态 官方": ["https://example.com/beacon-form"],
            "Quark 产品形态 官方": [],
            "Atlas Beacon Quark 产品形态": ["https://example.com/atlas-form", "https://example.com/beacon-form"],
            "Quark 补充 缺失维度": [],
        },
        "loops": {
            "Atlas": ("Atlas 产品形态 官方", ["https://example.com/atlas-form"], False),
            "Beacon": ("Beacon 产品形态 官方", ["https://example.com/beacon-form"], False),
            "Quark": ("Quark 产品形态 官方", [], True),
        },
        "claims": [
            {"subject": "Atlas", "dimension": "产品形态", "kind": "fact", "text": "Atlas 是面向分析师的研究工作台。", "verification": "fully"},
            {"subject": "Beacon", "dimension": "产品形态", "kind": "fact", "text": "Beacon 是实时监控面板。", "verification": "fully"},
            {"subject": "Quark", "dimension": "产品形态", "kind": "unknown", "text": "未找到 Quark 的原文来源。", "verification": "unconfirmed"},
        ],
        "baseline_claims": [
            {"subject": "Atlas", "dimension": "产品形态", "kind": "fact", "text": "Atlas 是研究工作台。", "verification": "partial"},
            {"subject": "Beacon", "dimension": "产品形态", "kind": "fact", "text": "Beacon 是监控面板。", "verification": "partial"},
            {"subject": "Quark", "dimension": "产品形态", "kind": "fact", "text": "Quark 与 Atlas 形态相同。", "verification": "unrelated"},
        ],
    },
    {
        "id": "conf-price",
        "family": "conflict",
        "question": "核实 Atlas 的定价，如有冲突请保留两个口径",
        "subjects": ["Atlas"],
        "dimensions": ["定价"],
        "baseline_query": "Atlas 定价",
        "pages": {
            "https://example.com/atlas-price": page(
                "https://example.com/atlas-price",
                "Atlas 官方定价",
                "官方写每月 40 美元。",
                "定价：Atlas Team 官方标价为每席每月 40 美元，2026 年 3 月更新。",
            ),
            "https://example.com/atlas-price-repost": page(
                "https://example.com/atlas-price-repost",
                "转载评测",
                "评测写每月 10 美元。",
                "定价：一篇转载评测写 Atlas 入门档每月只要 10 美元，未给出日期。",
            ),
        },
        "queries": {
            "Atlas 定价 官方": ["https://example.com/atlas-price", "https://example.com/atlas-price-repost"],
            "Atlas 定价": ["https://example.com/atlas-price", "https://example.com/atlas-price-repost"],
        },
        "loops": {
            "Atlas": ("Atlas 定价 官方", ["https://example.com/atlas-price", "https://example.com/atlas-price-repost"], False),
        },
        "claims": [
            {"subject": "Atlas", "dimension": "定价", "kind": "analysis", "text": "官方标价每席每月 40 美元，转载评测写 10 美元，两口径并存。", "verification": "partial"},
        ],
        "baseline_claims": [
            {"subject": "Atlas", "dimension": "定价", "kind": "fact", "text": "Atlas 每月只要 10 美元。", "verification": "contradicted"},
        ],
    },
    {
        "id": "conf-deploy",
        "family": "conflict",
        "question": "核实 Beacon 的部署方式，冲突不要合并",
        "subjects": ["Beacon"],
        "dimensions": ["部署方式"],
        "baseline_query": "Beacon 部署方式",
        "pages": {
            "https://example.com/beacon-deploy": page(
                "https://example.com/beacon-deploy",
                "Beacon 官方部署",
                "官方只提供云端。",
                "部署方式：Beacon 官方文档写明仅提供多租户云端，不提供私有化安装包。",
            ),
            "https://example.com/beacon-blog": page(
                "https://example.com/beacon-blog",
                "第三方博客",
                "博客称已有私有化。",
                "部署方式：一篇未标注日期的博客称 Beacon 已支持私有化部署，但未附官方链接。",
            ),
        },
        "queries": {
            "Beacon 部署方式 官方": ["https://example.com/beacon-deploy", "https://example.com/beacon-blog"],
            "Beacon 部署方式": ["https://example.com/beacon-deploy", "https://example.com/beacon-blog"],
        },
        "loops": {
            "Beacon": ("Beacon 部署方式 官方", ["https://example.com/beacon-deploy", "https://example.com/beacon-blog"], False),
        },
        "claims": [
            {"subject": "Beacon", "dimension": "部署方式", "kind": "analysis", "text": "官方只提供云端；博客称有私有化，来源冲突。", "verification": "partial"},
        ],
        "baseline_claims": [
            {"subject": "Beacon", "dimension": "部署方式", "kind": "fact", "text": "Beacon 已经全面支持私有化部署。", "verification": "contradicted"},
        ],
    },
    {
        "id": "conf-date",
        "family": "conflict",
        "question": "核实 NovaWriter 产品形态的发布时间",
        "subjects": ["NovaWriter"],
        "dimensions": ["产品形态"],
        "baseline_query": "NovaWriter 产品形态 发布",
        "pages": {
            "https://example.com/nova-2024": page(
                "https://example.com/nova-2024",
                "新闻稿 2024",
                "2024 年发布写作工作台。",
                "产品形态：2024 年 6 月新闻稿称 NovaWriter 以浏览器写作工作台形式发布。",
            ),
            "https://example.com/nova-2025": page(
                "https://example.com/nova-2025",
                "媒体转载 2025",
                "2025 年才上线。",
                "产品形态：2025 年一篇转载称 NovaWriter 到 2025 年 1 月才正式上线写作工作台。",
            ),
        },
        "queries": {
            "NovaWriter 产品形态 发布 官方": ["https://example.com/nova-2024", "https://example.com/nova-2025"],
            "NovaWriter 产品形态 发布": ["https://example.com/nova-2024", "https://example.com/nova-2025"],
        },
        "loops": {
            "NovaWriter": ("NovaWriter 产品形态 发布 官方", ["https://example.com/nova-2024", "https://example.com/nova-2025"], False),
        },
        "claims": [
            {"subject": "NovaWriter", "dimension": "产品形态", "kind": "analysis", "text": "一则来源写 2024 年 6 月发布写作工作台，另一则写 2025 年 1 月上线。", "verification": "partial"},
        ],
        "baseline_claims": [
            {"subject": "NovaWriter", "dimension": "产品形态", "kind": "fact", "text": "NovaWriter 于 2025 年才首次发布。", "verification": "contradicted"},
        ],
    },
]


def plan_message(task: dict) -> dict:
    plan = {
        "goal": task["question"],
        "subjects": task["subjects"],
        "dimensions": task["dimensions"],
        "questions": [f"收集{subject}的直接来源" for subject in task["subjects"]],
        "max_search_calls": 24,
        "max_gap_rounds": 1,
    }
    return {"role": "assistant", "content": json.dumps(plan, ensure_ascii=False), "_tokens": 16}


LIVE_VERDICTS = {"fully", "partial", "contradicted", "unrelated"}


def _policy(rows: list[dict]) -> dict:
    return {
        "claims": [{key: row[key] for key in ("subject", "dimension", "kind", "text")} for row in rows],
        "verify": [
            {"subject": row["subject"], "dimension": row["dimension"], "verification": row["verification"]}
            for row in rows
            if row["verification"] in LIVE_VERDICTS
        ],
    }


def compile_task(task: dict) -> dict:
    pages = task["pages"]
    tavily = {}
    for query, urls in task["queries"].items():
        tavily[query] = result_pack(*(pages[url] for url in urls))
    model = {"plan": plan_message(task)}
    for subject, (query, urls, empty_round) in task["loops"].items():
        model.update(research_loop(subject, query, urls, empty_round))
        for key, value in list(model.items()):
            if isinstance(value, dict) and "_tokens" not in value:
                value["_tokens"] = 8
    model["plan"]["_tokens"] = 16
    page_subjects = {}
    for subject, (_query, urls, _empty) in task["loops"].items():
        for url in urls:
            page_subjects[url] = subject
    return {
        "task": {
            **{key: task[key] for key in ("id", "family", "question", "subjects", "dimensions", "baseline_query")},
            "page_subjects": page_subjects,
        },
        "tavily": tavily,
        "model": model,
        "policies": {"workbench": _policy(task["claims"]), "baseline": _policy(task["baseline_claims"])},
    }
