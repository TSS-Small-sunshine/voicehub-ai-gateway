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
cp .env.example .env   # 至少填 VOICEHUB_*、LLM_API_KEY、ADMIN_SECRET（见安全手册）
docker compose up -d   # caddy(443 唯一入口) + ai-gateway 自愈
```

启动后：浏览器访问 `https://<主机IP>`（自签证书需信任一次）→ 用 `.env` 里的 `ADMIN_INIT_USER/PASS` 首登 → 强制改密 → 「供应商」页配 LLM → 「设置」页按需调整。

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
| `GATEWAY_DATABASE_URL` | | 管理台库（留空=与上同库，推荐默认） |
| `ADMIN_SECRET` | ✅ | **P0**：加密根密钥（Fernet+HMAC），`openssl rand -hex 32` 生成，见下方安全手册 |
| `ADMIN_INIT_USER` / `ADMIN_INIT_PASS` | ✅首启 | 首启管理员（创建后可删）；首登强制改密 |
| `SITE_ADDR` | | Caddy 站点地址；有域名时设为域名自动签 Let's Encrypt（默认 `:443` 自签） |
| `LOG_LEVEL` | | 默认 INFO |

更多运行期参数（抽查周期、置信阈值、语种白名单、日志保留天数、冻结注册通道等）登录管理台「⚙️ 设置」页热调整，存 DB 优先级高于 env。

---

## 🔐 安全部署手册（校方 IT 必读）

### P0：ADMIN_SECRET 的生成、保管与轮换

`ADMIN_SECRET` 是整个系统的加密根：供应商 API Key 用它做 Fernet 加密、名册学号用它做 HMAC-SHA256。**泄露 = 全部密钥与实名数据失守**。

1. **生成**（任一方式）：`openssl rand -hex 32` 或 PowerShell `-join ((1..64) | ForEach-Object { '{0:x}' -f (Get-Random -Max 16) })`
2. **保管**：写入服务器 `.env`（gitignore 内）；同时在离线介质（打印/U盘保险柜）留存一份副本。严禁进 git、日志、聊天工具。
3. **轮换**（怀疑泄露或定期）：
   ```bash
   # a) 旧 Key 启动；b) 导出新 Fernet 重加密存量 API Key（供应商页逐个重存即可）；
   # c) 名册学号 HMAC 为确定性派生——轮换后需整册重新导入（管理台重传 CSV，幂等覆盖）；
   # d) 换 .env 并 docker compose up -d。
   ```
4. **验证**：未配置时系统拒绝加密存储并显式报错（不静默降级明文）；`/health` 会如实反映 `admin_secret_configured`。

### 备份与恢复

- **SQLite 快照**：管理台「归档」任务已周期把两库快照到 `data/archive/*.db`（保留 N 份可配）。手工备份：
  `sqlite3 data/gateway.db ".backup 'backup/gateway_$(date +%F).db'"`
- **恢复**：停服务 → 用快照覆盖 `data/*.db` → 启动。演练建议每学期一次。
- **切 PostgreSQL**：出现并发瓶颈时仅改两个连接串（`DATABASE_URL`/`GATEWAY_DATABASE_URL`）即迁移，全链路 SQLAlchemy ORM 无需改代码；备份走 `pg_dump`。

### 暴露面与容器基线

- 唯一入口 Caddy 443（HTTP 自动跳 HTTPS + HSTS）；应用端口**不**对宿主机暴露；
- 有域名：`.env` 设 `SITE_ADDR=your.domain.com` 自动签发续期证书；内网无域名用自签（导入信任一次）；
- 出站仅两条：主仓 `/api/open/*`（X-API-Key）、LLM/Tavily（文本已脱敏）；学生数据永不外送；
- 数据全部本地落盘 SQLite；`data/` 卷独立挂载便于备份迁移。

### 学生个人信息保护清单

- 送 AI 平台前强制脱敏（姓名/手机/QQ/学号/身份证/URL），日志只落脱敏文本，原文不落盘；
- 名册学号以 HMAC 存储（不可逆），姓名仅 admin 可见且每次导入留审计；
- viewer 角色只见脱敏视图；高位操作（规则/复核/供应商/风控/设置）全量审计留痕；
- 日志保留期默认 180 天自动清理；抽查不自动处置学生账号，异常仅标记待人工。

---

## 目录结构

```
voicehub-ai-gateway/
├── app/
│   ├── main.py              # FastAPI 入口（四后台任务 lifespan 挂载）
│   ├── config.py            # 环境变量配置
│   ├── settings.py          # 运行期设置 DB>env>默认，热生效
│   ├── auth.py              # 管理台账号/会话/限流/CSRF
│   ├── security.py          # argon2/Fernet/HMAC/TOTP/token
│   ├── mask.py              # PII 脱敏统一实现
│   ├── roster.py            # 名册 CSV/HMAC/备注比对
│   ├── voicehub_client.py   # 主仓 /api/open/* 客户端
│   ├── db.py                # 双库 ORM + make_engine(WAL/busy_timeout)
│   ├── admin/               # Web 管理台（认证路由+队列/日志/规则/供应商/名册/抽查/风控/设置）
│   ├── providers/           # 供应商 registry/service（Fernet 加密 Key、主备熔断）
│   ├── reviewers/           # l1_rules / l2_llm / l3_search / language_detector
│   ├── prompts/             # 分场景 prompts
│   ├── rules/               # L1 内置规则
│   └── workers/             # poll_pending / spotcheck / archive / cleanup
├── docs/submissions/        # AI 初审导读等送审材料
├── data/                    # 本地数据卷：两库 + archive/ 快照（gitignore）
├── tests/                   # 全离线 pytest + 压测
├── Caddyfile                # TLS 唯一入口配置
├── docker-compose.yml       # caddy + ai-gateway（健康检查自愈）
└── .env.example
```

---

## 上线路径

- **Phase 1**：L1 规则引擎接三场景（零成本，立即可用）
- **Phase 2**：L2 LLM 接入（注册年级核对 + 留言）
- **Phase 3**：歌曲 + L3 联网（语种）

## 安全合规

- **学生信息强制脱敏**：送 LLM/L3 的文本与落盘日志全部经 `app/mask.py`（姓名保留姓氏、手机/QQ/学号/身份证/URL 掩码），原文不落盘；管理台对 viewer 仅展示脱敏视图
- **Prompt 注入**：用户内容当数据，系统提示固化"只输出 JSON"，解析失败一律 REVIEW 兜底
- **审计**：所有 decision / model / durationMs / dataSource 入库可追踪；管理操作全量审计留痕
- **注册实名比对**：名册学号 HMAC 匹配 + 备注姓名核对，不一致/编造一律转人工复核，绝不自动处置

## License

MIT