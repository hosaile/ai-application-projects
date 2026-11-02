# AI Application Projects

三个 AI 应用项目：设备运维知识库 RAG 问答、校园报修 AI 分诊、网络排障助手。

## 1. 设备运维知识库 RAG 问答系统（`rag-knowledge-base/`）

- 检索增强生成：BGE 中文向量 + Chroma 向量库 + 大模型生成，回答附带来源章节与相似度
- 知识库文档可在页面上传、删除并一键重建索引；支持多轮对话（含指代追问）
- 防幻觉：提示词只允许依据检索资料、强制 JSON 输出校验、模型不可用时降级为检索模式
- 评估：34 题测试集，Recall@5 **0.809** / Hit@5 **0.853** / MRR **0.734**

```bash
pip install chromadb sentence-transformers flask openai
python tools/build_index.py
python app.py          # http://127.0.0.1:8100/
```

## 2. 校园报修 AI 分诊系统（`campus-repair-ai/`）

- 一句话报修 → 模型分诊（类别 / 优先级 / 自查步骤 / 置信度）→ 工单流转 → 统计看板
- Flask + SQLite + 原生前端；支持 OpenAI 兼容与 Anthropic 兼容两种 API；失败自动降级规则引擎
- 工程质量：接口回归 43 项、元审计 29 项（独立复算 / 并发 / 注入 / 边界）
- 可打包为 Windows 桌面软件（PyInstaller）

```bash
pip install flask openai requests
python seed.py
python app.py          # http://127.0.0.1:8000/
```

## 3. 网络排障助手（`network-troubleshooting-assistant/`）

- 5 大类 23 个故障场景、141 步排查，单文件 HTML，手机端离线可用
- 附 AI 对话版提示词与使用说明

## 技术栈

Python · Flask · SQLite · Chroma · Sentence-Transformers（BGE）· 原生 HTML/CSS/JS · OpenAI 兼容 API

## 说明

- 所有演示数据均为虚构，不含真实用户信息。
- 仓库内不含任何 API Key、账号凭据或个人联系方式。
