# 离线评测结果

固定 10 题、每题重复 3 次，均值包含全部重复，不取最好一次。失败单独列出，不丢弃。
工作台跑完整研究图（含计划中断、read_source、缺口补充、综合、核验）。
基线 A 只做一次搜索 + 一次综合，搜索摘要入库且不打维度，不跑研究图。
两边同一预算：`max_model_calls=80`、`report_reserved_calls=4`、`max_search_calls=24`。
引用支持率来自核验节点标签（fully/partial），不是人工复标。demo 语义支持率记为 n/a。
冲突三题的结论是 analysis，没有 fact，引用完整率对这两列是 n/a，不编造。

导出产物 60 份。

- 工作台：覆盖 0.89，引用完整 1.00，引用支持 1.00，模型 9.00，搜索 2.00，Token 124.00
- 基线 A：覆盖 0.00，引用完整 1.00，引用支持 0.52，模型 2.00，搜索 1.00，Token 60.00

```
task                   fam         sys        rep   cov  cite  supp  mdl srch   tok status
------------------------------------------------------------------------------------------
comp-atlas-beacon      competitor  baseline   1    0.00  1.00  1.00    2    1    60 partial
comp-atlas-beacon      competitor  baseline   2    0.00  1.00  1.00    2    1    60 partial
comp-atlas-beacon      competitor  baseline   3    0.00  1.00  1.00    2    1    60 partial
comp-atlas-beacon      competitor  workbench  1    1.00  1.00  1.00    9    2   124 completed
comp-atlas-beacon      competitor  workbench  2    1.00  1.00  1.00    9    2   124 completed
comp-atlas-beacon      competitor  workbench  3    1.00  1.00  1.00    9    2   124 completed
comp-chatgpt-gemini    competitor  baseline   1    0.00  1.00  0.67    2    1    60 partial
comp-chatgpt-gemini    competitor  baseline   2    0.00  1.00  0.67    2    1    60 partial
comp-chatgpt-gemini    competitor  baseline   3    0.00  1.00  0.67    2    1    60 partial
comp-chatgpt-gemini    competitor  workbench  1    1.00  1.00  1.00    9    2   124 completed
comp-chatgpt-gemini    competitor  workbench  2    1.00  1.00  1.00    9    2   124 completed
comp-chatgpt-gemini    competitor  workbench  3    1.00  1.00  1.00    9    2   124 completed
comp-claude-gemini     competitor  baseline   1    0.00  1.00  0.50    2    1    60 partial
comp-claude-gemini     competitor  baseline   2    0.00  1.00  0.50    2    1    60 partial
comp-claude-gemini     competitor  baseline   3    0.00  1.00  0.50    2    1    60 partial
comp-claude-gemini     competitor  workbench  1    1.00  1.00  1.00    9    2   124 completed
comp-claude-gemini     competitor  workbench  2    1.00  1.00  1.00    9    2   124 completed
comp-claude-gemini     competitor  workbench  3    1.00  1.00  1.00    9    2   124 completed
comp-cursor-copilot    competitor  baseline   1    0.00  1.00  1.00    2    1    60 partial
comp-cursor-copilot    competitor  baseline   2    0.00  1.00  1.00    2    1    60 partial
comp-cursor-copilot    competitor  baseline   3    0.00  1.00  1.00    2    1    60 partial
comp-cursor-copilot    competitor  workbench  1    1.00  1.00  1.00    9    2   124 completed
comp-cursor-copilot    competitor  workbench  2    1.00  1.00  1.00    9    2   124 completed
comp-cursor-copilot    competitor  workbench  3    1.00  1.00  1.00    9    2   124 completed
conf-date              conflict    baseline   1    0.00   n/a  0.00    2    1    60 partial
conf-date              conflict    baseline   2    0.00   n/a  0.00    2    1    60 partial
conf-date              conflict    baseline   3    0.00   n/a  0.00    2    1    60 partial
conf-date              conflict    workbench  1    1.00   n/a  1.00    7    1   108 completed
conf-date              conflict    workbench  2    1.00   n/a  1.00    7    1   108 completed
conf-date              conflict    workbench  3    1.00   n/a  1.00    7    1   108 completed
conf-deploy            conflict    baseline   1    0.00   n/a  0.00    2    1    60 partial
conf-deploy            conflict    baseline   2    0.00   n/a  0.00    2    1    60 partial
conf-deploy            conflict    baseline   3    0.00   n/a  0.00    2    1    60 partial
conf-deploy            conflict    workbench  1    1.00   n/a  1.00    7    1   108 completed
conf-deploy            conflict    workbench  2    1.00   n/a  1.00    7    1   108 completed
conf-deploy            conflict    workbench  3    1.00   n/a  1.00    7    1   108 completed
conf-price             conflict    baseline   1    0.00   n/a  0.00    2    1    60 partial
conf-price             conflict    baseline   2    0.00   n/a  0.00    2    1    60 partial
conf-price             conflict    baseline   3    0.00   n/a  0.00    2    1    60 partial
conf-price             conflict    workbench  1    1.00   n/a  1.00    7    1   108 completed
conf-price             conflict    workbench  2    1.00   n/a  1.00    7    1   108 completed
conf-price             conflict    workbench  3    1.00   n/a  1.00    7    1   108 completed
miss-empty-raw         missing     baseline   1    0.00  1.00  0.50    2    1    60 partial
miss-empty-raw         missing     baseline   2    0.00  1.00  0.50    2    1    60 partial
miss-empty-raw         missing     baseline   3    0.00  1.00  0.50    2    1    60 partial
miss-empty-raw         missing     workbench  1    0.50  1.00  1.00    9    2   124 partial
miss-empty-raw         missing     workbench  2    0.50  1.00  1.00    9    2   124 partial
miss-empty-raw         missing     workbench  3    0.50  1.00  1.00    9    2   124 partial
miss-inkbot-price      missing     baseline   1    0.00  1.00  0.50    2    1    60 partial
miss-inkbot-price      missing     baseline   2    0.00  1.00  0.50    2    1    60 partial
miss-inkbot-price      missing     baseline   3    0.00  1.00  0.50    2    1    60 partial
miss-inkbot-price      missing     workbench  1    0.75  1.00  1.00   11    3   140 partial
miss-inkbot-price      missing     workbench  2    0.75  1.00  1.00   11    3   140 partial
miss-inkbot-price      missing     workbench  3    0.75  1.00  1.00   11    3   140 partial
miss-third-vendor      missing     baseline   1    0.00  1.00  1.00    2    1    60 partial
miss-third-vendor      missing     baseline   2    0.00  1.00  1.00    2    1    60 partial
miss-third-vendor      missing     baseline   3    0.00  1.00  1.00    2    1    60 partial
miss-third-vendor      missing     workbench  1    0.67  1.00  1.00   13    4   156 partial
miss-third-vendor      missing     workbench  2    0.67  1.00  1.00   13    4   156 partial
miss-third-vendor      missing     workbench  3    0.67  1.00  1.00   13    4   156 partial

means over all repeats (not the best run):
  baseline: n=30 cov=0.00 cite=1.00 support=0.52 model=2.00 search=1.00 tokens=60.00 failed=none
  workbench: n=30 cov=0.89 cite=1.00 support=1.00 model=9.00 search=2.00 tokens=124.00 failed=none
```
