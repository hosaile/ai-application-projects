# 基线冻结（Phase 0）

记录合并前三个项目的运行方式，便于回滚与对照。原项目均未改动。

## 源项目

| 项目 | 路径 | 启动 | 端口 | 数据 |
|---|---|---|---|---|
| RAG 知识库（毕设） | `毕设_RAG知识库系统/` | `python app.py` | 8100 | `data/docs`、`data/chroma`、`data/tests`、`data/feedback.jsonl` |
| 校园报修 | `ai-application-projects/campus-repair-ai/` | `python app.py` | 8000 | `tickets.db`、`config.json`、`kb_snippet.json` |
| 网络排障助手 | `ai-application-projects/network-troubleshooting-assistant/` | 浏览器打开 HTML | — | localStorage |

## 各自的关键路由

- RAG：`/`、`/api/ask`、`/api/docs`、`/api/upload`、`/api/rebuild`、`/api/stats`、`/api/eval`、`/api/settings/models`、`/api/settings/active`、`/api/settings/test`、`/api/feedback`
- 报修：`/`、`/admin`、`/api/health`、`/api/settings`、`/api/tickets`、`/api/tickets/<no>`、`/api/stats`、`/api/data/*`、`/api/ping`
- 助手：单文件，无路由

## 测试命令（合并前）

```bash
# RAG 体检（独立实例 8101，28 项）
python 毕设_RAG知识库系统/审计/rag_health_check.py

# 报修接口回归（独立实例 8765，43 项）
python ai-application-projects/campus-repair-ai/qa_api.py

# 报修元审计（29 项，仓库内脚本）
```

## 已知问题

- RAG 首次问答需加载 BGE 模型，冷启动约 20–60 秒；报修分诊接入 RAG 后同样受影响。
  → 统一版已加后台预热 + 检索 8 秒超时，超时退回规则分诊。
- 报修原设置页自带一套 Key；统一版不再单独保存，改为读取工作台 providers。
- 助手 localStorage 与统一版 `/field/assistant.html` 不同源，收藏记录不互通。
- 三个项目端口不同（8100/8000），合并后统一为 8100。

## 基线验收

- [x] 三个模块源码已复制到 `device-ops-workbench/modules/`
- [x] 知识库 9 篇、索引 89 块、评估结果可读
- [x] 报修 15 条演示数据可重置
- [x] 助手 HTML 原文件未修改（1317 行 / 130KB）
- [x] 原项目目录无改动
