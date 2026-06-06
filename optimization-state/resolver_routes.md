# Phase 13 Resolver — 任务→Skill路由表

> 设计原则：Thin Harness, Fat Skills — 不将所有Skill塞入System Prompt，按任务类型动态挂载

## 路由规则

| 任务类型 | 触发词 | 加载Skill |
|----------|--------|----------|
| 1688运营 | 网销宝/搜索排名/询盘/爆款/竞品/转化率/出价 | 1688-operation, research-engine |
| AI资讯 | AI日报/热点/资讯/新闻 | aihot |
| 数据分析 | 分析/数据/报表/统计/Excel | Excel/XLSX, research-engine |
| 代码开发 | 写代码/开发/编程/脚本/Python | ai-expert, llm-learning |
| 文档写作 | 写报告/写文章/PPT/公文 | content-writer, ima-report, ima-ppt |
| 知识管理 | 整理知识库/记笔记/导图 | ima-knowledge, ima-note, kb-synthesizer, mind-map-generator |
| 图像生成 | 画图/生图/做图/图表 | image-gen, text-to-svg |
| 技术调研 | 调研/行业分析/深度研究 | industry-analysis, industry-research |
| 自我进化 | 优化/改进/复盘/升级 | Self-Improving + Proactive Agent |
| 1688电商诊断 | 落地页/详情页/转化率优化 | 电商落地页转化审计 |
| 复杂编排 | 多步骤/协调/分解任务 | agent-orchestrator |
| 记忆查询 | 之前/上次/记录/记住 | agent-memory |

## 加载策略

```
1. 用户输入 → 匹配触发词 → 获取Skill列表
2. 仅加载匹配的Skill（最多3个），不加载全量
3. 基础工具（search/fetch/file_read等）始终可用
4. 未匹配的任务 → 只加载通用工具，不加载任何Skill
```

## 优先级

- 1688运营 > 自我进化 > 数据分析 > 其他
- 同一任务匹配多个Skill时，按优先级取前3个