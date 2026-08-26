---
feature: web-admin
status: designed
updated: 2026-08-26
branch: feat/web-admin
---

# VoiceHub AI Gateway Web 管理台（统一运营 + 安全加固）

## Report

（交付时填写）

## [S1] 问题

voicehub-ai-gateway 当前仅有轮询 worker + /health，无任何管理入口：规则只能改文件、场景开关只能改 .env、审核结果/日志只能查 SQLite、REVIEW 项无人力复核通道。交付校方自行部署后，校方管理员需要一个统一运营台完成日常审核与配置，且必须满足：多国内 AI 供应商兼容、配置便捷、注册备注风险评估可靠并支持定期抽查、发往国内 AI 平台前学生信息强制脱敏。

## [S2] 设计总览

单服务扩展：在现有 FastAPI 上新增 `/admin/*` 模块（Jinja2 + 本地静态资源，零 CDN 离线可交付）+ Caddy sidecar 作唯一 TLS 入口；轮询 worker 保留并接入运行期配置。数据落 SQLite（`data/gateway.db`：用户/会话/设置/规则/供应商/审计/抽查状态），与既有 `ai_review.db`（脱敏后的审核日志）分离。

图：

```
浏览器 ──HTTPS(443)──► Caddy（唯一入口，TLS 终止 + 强制跳转）
                         │ HTTP（容器内网）
                         ▼
                    FastAPI app
                      ├─ /admin/*        管理台（登录+角色）
                      ├─ /health         探活
                      └─ 轮询 worker ──HTTPS──► VoiceHub /api/open/*（X-API-Key）
                                          └─HTTPS──► LLM 供应商 / Tavily（送审文本已脱敏）
```

## [S3] 部署拓扑与传输加密

- Caddy 终止 TLS：有域名 → Let's Encrypt 自动签发续期；内网 IP/无域名 → 启动自签证书；HTTP 一律 301 → HTTPS + HSTS
- 网关 app 只监听容器网络，不暴露裸端口
- 出站链路全 HTTPS：VoiceHub（X-API-Key，Key 仅环境变量注入，不出现在 UI/日志）、LLM 供应商、L3 搜索
- docker-compose 增加 `caddy` 服务；SQLite 卷独立挂载（compose volume）

## [S4] 认证与会话（本地账号 + 角色）

- `gw_users`：argon2 哈希；首启初始化管理员（一次性环境变量/CLI），首登强制改密
- 角色：`admin`（全部）/ `reviewer`（队列·复核·日志·规则）/ `viewer`（只读）
- 会话：32B token 存 `gw_sessions`，Cookie HttpOnly + Secure + SameSite=Strict，12h 过期 + 30min 空闲失效
- 登录防护：5 次/15 分钟滑动限流 + 账号锁定；CSRF（SameSite=Strict + X-CSRF-Token 请求头校验）；安全响应头
- 可选 TOTP 2FA（管理员启用，pyotp）
- 全部管理 API 收口 `/admin/*` + 独立鉴权中间件，与 API Key 体系互斥不干扰

## [S5] 运营台功能

| 页面 | 能力 |
|---|---|
| 看板 | 各场景待审/已审/REVIEW 统计、LLM 调用量与成功率、延迟 p50/p95、抽查状态摘要 |
| 待审队列 | 按场景拉主仓 pending（原文仅内存展示给 reviewer/admin）；人工复核 APPROVE/REJECT/REVIEW 写回主仓（reason 标注「人工」），REVIEW 项可改规则后手动重审 |
| 审核日志 | 检索（场景/时间/decision/模型/耗时/数据源）；脱敏视图与（admin）脱敏前对照 |
| 规则管理 | L1 关键词/正则 CRUD（含 skip_scenes），命中预览，保存即热生效 |
| 供应商管理 | 见 [S7] |
| 抽查 | 见 [S8] |
| 设置 | 场景开关、轮询间隔/批大小、REVIEW 冷却、L2 置信阈值、L3 开关、语种白名单、抽查参数 |
| 系统 | 管理员/角色管理、TOTP、操作审计、日志导出（CSV） |

## [S6] 数据安全与脱敏（必须项）

- **强制脱敏**（校方硬要求）：`mask.py` 统一实现——姓名（保留姓氏+`*`）、手机号（`138****5678`）、QQ（保留前 3 后 2）、学号/连续数字、身份证、URL；作用于：
  1）送 LLM/L3 的文本（`build_review_text` 产物在出站前 mask）
  2）`ai_review_logs.payload_json` 只落脱敏文本，原文不落盘
- 管理台队列对 reviewer/admin 显示原文（内存窗口，不落盘），viewer 仅脱敏视图——校方内部人工复核需要原文判断
- DB 内密钥（供应商 API Key，见 [S7]）用 Fernet 加密存储（`cryptography`），主密钥来自环境变量 `ADMIN_SECRET`；未设置该变量时供应商 Key 仅允许 env 注入模式
- SQLite 权限容器内 600；备份指引（sqlite3 .backup / 卷快照）
- `gw_audit_logs`：管理操作（登录/规则/开关/复核/供应商/抽查）只追加留痕，含前后值

## [S7] AI 供应商管理（多国内平台兼容 + 便捷配置）

- **统一 OpenAI 兼容对接**：DeepSeek / GLM（智谱）/ Kimi（月之暗面）/ 通义千问（阿里）/ MiniMax / 小米 MiMo / 硅基流动等全部 OpenAI 兼容，`{base_url, api_key, model}` 三元组即可接入；`l2_llm.py` 保持 AsyncOpenAI 统一调用，无需按供应商分叉
- `gw_providers` 表：预设模板（内置上述厂商的默认 base_url/model 清单）+ 自定义条目；字段：名称、base_url、model、api_key（Fernet 加密）、优先级/启用、超时、max_tokens；管理台下拉即切换默认供应商，热生效
- **两种密钥注入模式**：① env 注入（`LLM_API_KEY` 等，最安全，校方 IT 习惯）；② 管理台配置（加密落库，便捷切换）；两者并存，env 优先
- 非 OpenAI 兼容供应商（若有）预留 adapter 接口（`providers/` 目录），当前不实现

## [S8] 定期抽查机制（备注风险评估 + 抽样复审）

- **注册备注风险评估强化**：审核文本=用户名/姓名/备注（L1 剥离裸数字规则防学号误杀，skip_scenes 已实现）；L2 注册 prompt 维持「备注违规→REJECT、年级交叉不一致→REVIEW、合规→APPROVE」；LLM 输出加固：抽样信用评分字段 `noteRisk`（高/中/低）可选用，高→REVIEW
- **抽查（自动定期）**：`gw_spotcheck` 配置（周期天数、每批条数或比例、是否含 reviewer 已复核项）；独立低频任务从「已判定通过」的记录（register 已 active / note approved）按时间抽样，重新送 LLM 复审并记录 `gw_spotcheck_logs`（对象、原判定、复审判定、置信度、模型、时间）
- **抽查不一致处置**：不一致且复审为 REJECT → 仅标记「待人工复核」推送管理台抽查页，**绝不自动写回/删号**（人工确认后才动作）——安全第一，防误杀
- 抽查结果进看板摘要 + 日志可筛「抽查」

## [S9] 配置存储与轮询适配

- 设置存 `gw_settings`，优先级 **DB > 环境变量 > 内置默认**；worker 每轮重读（`get_scenes()` 已按轮读取，配套扩展）
- 既有审计修复随分支带入：REVIEW 冷却去重、SCENES 收敛（默认 register/note）、无 LLM Key「仅 L1」提示、L1 skip_scenes
- 人工复核写回复用主仓 `result` 端点（pending 守卫保证并发安全），无新表/新字段

## [S10] 交付形态

- `docker-compose.yml` 增加 `caddy`；单 `docker compose up -d` 起全部
- 前端 Jinja2 + 本地静态资源（Tailwind 本地打包，零外网依赖）
- 交付物：镜像 + `.env.example`（含 `ADMIN_SECRET` 说明）+ README「安全部署手册」（端口/防火墙/证书/备份/改密/供应商接入）

## [S11] 测试与分阶段

- 离线 pytest 扩展：认证（限流/会话/CSRF/角色矩阵）、脱敏 mask 单测（含边界）、供应商配置 CRUD 与密钥加解密、抽查抽样逻辑、规则 CRUD、设置优先级、REVIEW 冷却
- 阶段：**A** 认证+骨架+看板+供应商管理 → **B** 队列+人工复核+日志检索+规则管理 → **C** 抽查+统计+设置热生效+交付文档

## Out of Scope

- 不做落盘整库加密（决策：脱敏存储即可）；不做主仓管理端改造（权限枚举等已在主仓分支修复）
- 不实现非 OpenAI 兼容供应商 adapter（预留接口）
- 抽查不自动删号/自动写回（仅标记人工复核）

## Tasks

- [ ] T-A1: 认证模块（gw_users/sessions/argon2/限流/CSRF/角色/TOTP 可选）— acceptance: pytest 认证用例绿，登录/登出/角色访问控制可用 (covers: S4)
- [ ] T-A2: /admin 框架（Jinja2 布局+本地静态资源+鉴权中间件+看板聚合页）— acceptance: 登录后看板展示统计 (covers: S3,S5)
- [ ] T-A3: 供应商管理（gw_providers + Fernet 加密 + 预设模板 + l2_llm 读库配置热生效）— acceptance: 管理台可增删改供应商并切换默认，pytest 密钥加解密绿 (covers: S7)
- [ ] T-B1: 待审队列 + 人工复核写回（复用主仓 result，原文内存展示，reason 标注「人工」）— acceptance: 队列按场景展示，复核动作写回成功且日志留痕 (covers: S5,S9)
- [ ] T-B2: 审核日志检索 + CSV 导出 + 脱敏视图 — acceptance: 检索过滤可用，payload 为脱敏文本 (covers: S5,S6)
- [ ] T-B3: 规则管理 CRUD（含 skip_scenes）+ 命中预览 + 热生效 — acceptance: 新增规则立即在 L1 生效（pytest + 手工） (covers: S5,S6,S9)
- [ ] T-C1: 设置页（场景/轮询/冷却/阈值/L3/语种白名单）DB>env 优先级热生效 — acceptance: 改设置后 worker 下一轮生效 (covers: S9)
- [ ] T-C2: 定期抽查（gw_spotcheck 配置 + 抽样任务 + 抽查页 + 看板摘要）— acceptance: 抽样日志入库、不一致项仅标记人工 (covers: S8)
- [ ] T-C3: 交付（compose+caddy、.env.example 扩展、安全部署手册、README 同步）— acceptance: docker compose up 一键起，文档与实际一致 (covers: S3,S10)