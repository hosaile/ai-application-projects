# 数据迁移与回滚

## 迁移表

| 数据 | 来源 | 目标 | 方式 |
|---|---|---|---|
| RAG 文档 | `毕设_RAG知识库系统/data/docs` | `data/docs` | 复制，不改原文件 |
| 向量索引 | `data/chroma` | `data/chroma` | 复制；文档变更后可重建 |
| 评估集/结果 | `data/tests` | `data/tests` | 复制；论文指标不变 |
| 报修工单 | `campus-repair-ai/tickets.db`（如有） | `data/tickets.db` | 复制；启动自动 `ALTER TABLE` 加 `ai_sources`/`asset_id` |
| 报修配置 | `campus-repair-ai/config.json` | 不迁移 | Key 不复制；统一 providers 重新填 |
| 报修知识 | `kb_snippet.json` | `modules/repair/kb_snippet.json` | 原样保留作规则兜底 |
| 现场助手 | `网络排障助手.html` | `modules/field/` | 原文件复制，不修改 |
| 设备台账 | 手工/CSV | `assets` 表 | `docs/assets-import-template.csv` |
| 工单经验 | 新增 | `data/docs/experience/` | Phase 3，审核脱敏后写入 |

## 迁移步骤

1. 备份原项目：`cp -r campus-repair-ai /backup/`，RAG 打 tag `v1.3`。
2. 复制数据（见上表）；不要复制任何 `config.json` 中的 Key。
3. 启动 `python app.py`，自动建表与迁移字段。
4. 跑 `python tests/test_workbench.py`，确认 76 项通过。
5. 打开 `/repair/admin`，核对工单数、分类分布、平均处理时长与原系统一致。

## 回滚

- 统一版出问题：停掉 8100，原三个项目按各自 README 启动即可；数据文件未原地修改。
- 代码回滚：每个 Phase 结束打 tag；`git reset --hard <tag>`。
- 数据回滚：`/repair/api/data/backup` 下载的 SQLite 快照覆盖 `data/tickets.db`。
- 演示模式：`/repair/api/demo/enter` 使用 `data/demo/tickets_demo.db`；退出即恢复真实库，不会写坏真实数据。

## Key 与隐私

- `config.json` 已加入 `.gitignore`；仓库只保留 `config.example.json`（Key 为空）。
- 迁移时不复制报修旧 Key；统一版只认 `providers[].api_key`。
- 工单含姓名/联系方式时，仅本机数据库保存；相似工单接口不返回 reporter/contact。
- 打包发布前检查：`config.json`、`data/tickets.db`、`data/demo/`、`desktop.log` 均不进压缩包。
