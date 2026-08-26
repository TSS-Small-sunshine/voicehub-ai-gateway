# VoiceHub AI Gateway

> **独立部署的 AI 审核网关** — 为 [VoiceHub](https://github.com/laoshuikaixue/VoiceHub)（校园广播站点歌系统）提供 L1 规则 + L2 LLM + L3 搜索三级漏斗初审能力。

**核心原则**：本仓库是 **VoiceHub 主仓库的纯外置服务**，**不 fork、不修改主仓库**。VoiceHub 主仓库通过现有 API Key 体系（`/api/open/*`）调用网关；网关主动从 VoiceHub 拉待审数据 → LLM/搜索判定 → POST 写回结果。

技术栈：**Python 3.12 + FastAPI + Docker**。

---

## 架构

```
┌─────────────────────────┐    HTTP + X-API-Key    ┌──────────────────────────┐
│   VoiceHub 主仓库        │ ←─────────────────────→ │  voicehub-ai-gateway（本仓）│
│                         │                          │                          │
│  /api/open/ai-review/    │                          │  ┌────────────────────┐  │
│    pending-list   (GET)  │  ← 拉待审（含 AI 权限） │  │  L1 规则引擎         │  │
│    result          (POST)│  ← 写回结果            │  │  L2 LLM 判定         │  │
│                         │                          │  │  L3 搜索（可选）     │  │
│  注册/歌曲/留言 pending  │  （预留）webhook 即时通知  │  └────────────────────┘  │
│    触发器                │   当前实现为轮询          │  审核日志 SQLite/PG       │
└─────────────────────────┘                          └──────────────────────────┘
```

## 三级漏斗

| 级别 | 内容 | 成本 | 数据源 |
|---|---|---|---|
| L1 | 关键词黑名单 / 正则（手机号/QQ/微信号/URL/引流话术） | 零 | 本地规则文件 `app/rules/` |
| L2 | LLM 结构化判定 `{decision, reason, confidence}` | 低 | OpenAI 兼容 LLM（DeepSeek/GLM/Kimi/通义千问…） |
| L3 | 联网搜索（仅语种 L2 低置信时触发） | 中 | Tavily（SearXNG 预留） |

**默认兜底**：任何异常 → REVIEW（保持 pending 转人工）。**绝不因网关故障卡死业务**。

---

## 场景

| scene | 审核内容 |
|---|---|
| `register` | 注册：用户名/姓名/备注/年级交叉核对 |
| `song` | 歌曲投稿：标题/歌手/备注（违规检测） |
| `note` | 公开留言：文本（辱骂/隐私泄露） |
| `language` | 语种：标题/歌手（歌曲语言判定） |

---

## 快速开始

### Docker（推荐）

```bash
git clone https://github.com/TSS-Small-sunshine/voicehub-ai-gateway.git
cd voicehub-ai-gateway
cp .env.example .env   # 填 LLM_API_KEY + VOICEHUB_API_KEY
docker-compose up -d
```

### 本地 Python

```bash
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
```

### 测试（离线）

```bash
.venv\Scripts\python -m pytest -q   # Windows；全部用例离线运行（fake LLM/搜索），不触网
```

---

## 环境变量

| 变量 | 必填 | 说明 |
|---|---|---|
| `VOICEHUB_API_BASE_URL` | ✅ | VoiceHub 地址，如 `https://voicehub.tssplus.top` |
| `VOICEHUB_API_KEY` | ✅ | VoiceHub 管理后台 → API Key 管理创建，勾选 `ai-review:read`+`ai-review:write` |
| `LLM_API_KEY` | ✅ | OpenAI 兼容接口 Key（DeepSeek/GLM/Kimi/通义千问） |
| `LLM_BASE_URL` | ✅ | 如 `https://api.deepseek.com/v1` |
| `LLM_MODEL` | ✅ | 如 `deepseek-chat` |
| `LLM_TIMEOUT_SECONDS` | | LLM 请求超时（默认 5） |
| `LLM_MAX_TOKENS` | | LLM 输出上限（默认 512） |
| `POLL_INTERVAL_SECONDS` | | 轮询间隔（默认 30） |
| `POLL_BATCH_SIZE` | | 单场景单轮拉取条数（默认 20） |
| `REVIEW_SCENES` | | 轮询场景，逗号分隔（默认 `register,note`；song/language 需 Phase 3 支持后再开） |
| `REVIEW_COOLDOWN_SECONDS` | | REVIEW 冷却（默认 300 秒，冷却期内同一待审项不重审） |
| `TAVILY_API_KEY` | | L3 搜索（语种低置信兜底），留空跳过 L3 |
| `DATABASE_URL` | | 审核日志（默认 `sqlite:///./data/ai_review.db`） |
| `LOG_LEVEL` | | 默认 INFO |

---

## 目录结构

```
voicehub-ai-gateway/
├── app/
│   ├── main.py              # FastAPI 入口
│   ├── config.py            # 环境变量配置
│   ├── voicehub_client.py   # 调用 VoiceHub /api/open/ai-review/* 的客户端
│   ├── db.py                # 审核日志 SQLite（默认）/PostgreSQL
│   ├── reviewers/
│   │   ├── l1_rules.py      # 关键词黑名单 + 正则
│   │   ├── l2_llm.py        # OpenAI 兼容 LLM 调用
│   │   ├── l3_search.py     # Tavily / SearXNG（可选）
│   │   └── language_detector.py  # 三级语种数据源
│   ├── prompts/             # 分场景 prompts
│   ├── rules/               # L1 规则文件
│   └── workers/
│       └── poll_pending.py  # 轮询 VoiceHub 待审 → 审核 → 写回
├── data/                    # SQLite 审核日志（容器挂载，gitignore）
├── tests/                   # pytest
├── .env.example
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
└── README.md
```

---

## 上线路径

- **Phase 1**：L1 规则引擎接三场景（零成本，立即可用）
- **Phase 2**：L2 LLM 接入（注册年级核对 + 留言）
- **Phase 3**：歌曲 + L3 联网（语种）

## 安全合规

- **学生信息脱敏**：送 LLM 前替换姓名/电话/QQ/学校为占位符（Phase 2 落地，当前直接透传）
- **Prompt 注入**：用户内容当数据，系统提示固化"只输出 JSON"
- **审计**：所有 decision / model / durationMs / dataSource 入库可追踪

## License

MIT