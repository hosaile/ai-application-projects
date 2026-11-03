# 架构

## 一个进程

```
浏览器
  │
  ▼
app.py（Flask，127.0.0.1:8100）
  ├── /                 modules/qa       RAG 工作台（对话/知识库/评估/模型设置）
  ├── /repair           modules/repair   学生端
  ├── /repair/admin     modules/repair   工单管理 + 资产 + 演示模式
  ├── /field            modules/field    包装页 + 原离线助手
  ├── /a/<code>         modules/repair   设备扫码档案
  ├── /start            角色首页
  └── /api/search       Ctrl+K 全局搜索
        │
        ├── core/paths.py     数据/配置/模型路径
        ├── core/config.py    providers 迁移、校验、脱敏、解析
        ├── core/llm.py       OpenAI / Anthropic / 会话头 / 参数回退
        └── core/logging.py   data/workbench.log
```

## 目录

```
core/                 共享层
modules/qa/           问答 + rag/（ingest/embed/store/retrieve/generate/evaluate）
modules/repair/       报修（routes/db/triage/demo_data/kb_snippet/static）
modules/field/        现场排障 HTML（不改原文件）
data/                 docs / chroma / tests / tickets.db / demo / feedback
models/               bge-small-zh-v1.5
tests/                test_workbench.py + mock_llm.py
tools/                build_index.py / run_eval.py
```

## 路由表

| 路径 | 方法 | 说明 |
|---|---|---|
| `/` | GET | 问答工作台 |
| `/api/ask` | POST | RAG 问答（带来源、模型选择） |
| `/api/docs` `/api/upload` `/api/docs/<name>` | GET/POST/DELETE | 知识库文档 |
| `/api/rebuild` `/api/stats` `/api/eval` `/api/feedback` | — | 索引/统计/评估/反馈 |
| `/api/settings/models` | GET/PUT | 统一供应商与模型（Key 不回传） |
| `/api/settings/active` | POST | 切换当前模型/思考等级 |
| `/api/settings/test` | POST | 连接测试 |
| `/repair/` | GET | 学生报修 |
| `/repair/api/tickets` | GET/POST | 工单列表/创建（AI 分诊） |
| `/repair/api/tickets/<no>` | GET/PATCH | 详情/状态流转/改类别（含 SLA 字段） |
| `/repair/api/tickets/<no>/experience` | GET/POST | 经验草稿/保存/重建索引 |
| `/repair/login` `/repair/logout` | GET/POST | 可选管理员口令 |
| `/repair/api/stats` | GET | 今日新增/待处理/平均时长/分类分布/已超时 |
| `/repair/api/metrics` | GET | AI 占比/SLA 达成/重复报修/经验文档 |
| `/repair/api/similar?q=` | GET | 相似已解决工单（脱敏） |
| `/repair/api/assets` | GET/POST | 设备台账 |
| `/repair/api/assets/<id>` | GET/PATCH/DELETE | 设备详情/编辑/删除 |
| `/repair/api/assets/<id>/qr.png` | GET | 二维码 PNG |
| `/repair/api/assets/by-code/<code>` | GET | 按编号查设备 |
| `/repair/api/demo/status|enter|exit` | GET/POST | 演示模式 |
| `/repair/api/data/info|backup|reset` | GET/POST | 数据管理 |
| `/field/` `/field/assistant.html` | GET | 现场排障 |
| `/a/<code>` | GET | 设备扫码页 |
| `/api/search?q=` | GET | 知识/工单/设备/操作 |

## 分诊数据流

```
描述 → RAG retrieve(top_k=3, 超时 8s)
     → 拼参考材料（文档/章节/片段）
     → 统一模型 chat(JSON)
     → category/priority/summary/checks/confidence/sources
     → 工单（ai_sources 存来源）
     → 任一步失败：关键词规则 + kb_snippet 兜底
```

## 资产感知检索

问题里出现设备编号、名称或位置时，QA 先查 `assets` 与最近工单，生成一条 `设备档案` 资料插到检索结果最前；
再拼普通文档检索结果。无 Key 时也会直接返回设备档案与历史工单原文。

## 经验反哺（KCS）

```
工单完成/关闭
  → 管理端「沉淀为经验」
  → 自动生成 Markdown（手机号/身份证/邮箱/姓名/房间号脱敏）
  → 管理员审核、补充结论
  → 写入 data/docs/experience/工单_<no>.md
  → 点「保存并重建索引」
  → 问答/分诊检索到，来源显示 experience/工单_xxx.md
```

不自动入库；未完成工单拒绝沉淀；重建失败只提示，不丢已保存文档。

## 配置

`config.json`（本地，gitignore）：

- `providers[]`：供应商（Base URL、格式、Key、超时、自定义 headers）
- `models[]`：模型（上下文、最大输出、思考等级、参数映射、能力）
- `active`：当前供应商/模型/思考等级
- `app/rag/repair/field`：端口、分诊 top_k、SLA 时限、助手文件名等
- `repair.sla_hours`：高/中/低优先级的小时数
- 管理员口令优先读环境变量 `WORKBENCH_ADMIN_PASSWORD`，也可写 `repair.admin_password`（不推荐）

Key 只在本机文件；`public()` 输出 `has_key`，`resolve()` 才把 Key 交给调用层。

## 兼容策略

- 旧 RAG `llm` 配置在读取时自动迁移为默认供应商。
- 旧报修 config 的 Key/模型不迁移；报修只读统一 providers。
- `/repair/api/settings` 保留 GET（返回当前模型信息），POST 返回 409 并指向工作台。
