---
feature: web-admin
status: designed
updated: 2026-08-27
branch: feat/web-admin
spec: docs/compose/specs/2026-08-26-web-admin-design.md
---

# Web 管理台实施计划（按 SPEC 阶段推进）

## 总览

按 SPEC [S1]-[S13] 与审计改进（MiMo 默认、SQLite 起步留 PG 弹性、ADMIN_SECRET P0、压测必做）落地。三阶段 + 交付。每阶段结束跑 pytest + commit。

## A 阶段：基础可用（认证骨架看板供应商）

依赖安装（一次）：

- 新增 `requirements-web.txt`：`fastapi==0.115.0`、`uvicorn[standard]==0.32.0`、`jinja2==3.1.4`、`itsdangerous==2.2.0`、`argon2-cffi==23.1.0`、`pyotp==2.9.0`、`cryptography==43.0.1`、`qrcode[pil]==7.4.2`、`python-multipart==0.0.12`、`pydantic[email]==2.9.2`

任务清单：

- [ ] A1: 扩展 `app/db.py`：新增 `GwUser/GwSession/GwAuditLog/GwProvider/GwSetting` 表（与 `AiReviewLog` 共库 `gateway.db`，SQLAlchemy declarative），init_db 兼容双库；`database_url` 兼容 PostgreSQL（不改连接串即可切）
- [ ] A2: `app/security.py`：argon2 密码、32B 随机 token、Fernet 加解密（基于 `ADMIN_SECRET`）、CSRF（itsdangerous 签名）、TOTP（pyotp）
- [ ] A3: `app/mask.py`：姓名/手机/QQ/学号/身份证/URL 脱敏（统一实现，单元测试覆盖边界）
- [ ] A4: `app/auth.py` + `app/admin/auth_routes.py`：首启初始化管理员（环境变量 `ADMIN_INIT_USER`/`ADMIN_INIT_PASS`，缺则日志提示）；登录/登出/改密；登录限流（5/15min，IP+账号双维度）；admin/reviewer/viewer 角色装饰器
- [ ] A5: `app/admin/static.py` + `app/admin/templates.py`：Jinja2 渲染 + 静态资源服务（Tailwind 本地包，`vendor/tailwind.css` 由构建脚本离线生成静态版）
- [ ] A6: `app/admin/decorators.py`：csrf_protect、require_role、login_required
- [ ] A7: `app/admin/routes_dashboard.py`：统计看板聚合（场景待审/已审/REVIEW、LLM 调用次数与成功率/延迟 p50/p95，按 AiReviewLog 聚合）
- [ ] A8: `app/providers/registry.py`：预设模板（MiMo/DeepSeek/Kimi/通义/GLM/MiniMax/硅基流动 + Ollama 本地），含默认 base_url 与模型
- [ ] A9: `app/providers/service.py`：gw_providers CRUD（Fernet 加密 Key）、主备关系、热生效（worker 每轮读 db）
- [ ] A10: `app/l2_llm.py`：超时/重试 3 次指数退避/熔断切换备用（读写 gw_providers，备用切换阈值 N 次连续失败）、缺 Key 仍按已降级工作
- [ ] A11: `app/admin/routes_providers.py`：管理台供应商 CRUD UI + 模板下拉
- [ ] A12: `app/admin/templates/login.html`、`base.html`、`dashboard.html`、`providers.html`
- [ ] A13: pytest：A1-A11 单测（加密/限流/脱敏/角色/auth），目标 ≥40 用例全绿

验证：

- `pytest -q` A 阶段用例全绿
- 手工：初始化管理员 → 登录 → 看板展示（mock 数据）→ 供应商增删改 → 切换默认供应商
- 提交 commit

## B 阶段：日常运营（队列+人工复核+日志+规则）

- [ ] B1: `app/admin/routes_review_queue.py`：拉主仓 pending 按场景，原文仅内存展示给 reviewer/admin，10 分钟过期强制刷新
- [ ] B2: `app/admin/routes_review_action.py`：人工复核写回主仓 `result`，reason 标注「人工」，审计留痕
- [ ] B3: `app/admin/routes_logs.py`：审核日志检索（场景/时间/decision/模型/耗时），CSV 导出，viewer 仅脱敏视图、admin 可看脱敏前对照
- [ ] B4: `app/admin/routes_rules.py`：L1 关键词/正则 CRUD（含 skip_scenes），命中预览，保存即热生效（改 l1_rules 内存配置）
- [ ] B5: `app/admin/templates/queue.html`/`log.html`/`rules.html`
- [ ] B6: pytest：B1-B4 单测（队列路由/人写回/规则 CRUD），目标 ≥25 用例

验证：

- pytest 全绿
- 手工：注册一个测试用户 → AI 判定 REVIEW → 队列出现 → 人工 APPROVE/REJECT/REVIEW 写回主仓 → 日志检索可见
- 提交

## C 阶段：完善交付（抽查+设置+归档+容错+压测）

- [x] C1: `app/settings.py`：gw_settings DB>env>默认优先级，热生效（worker 重读）
- [x] C2: `app/admin/routes_settings.py`：场景/轮询/批大小/冷却/置信阈值/L3/语种白名单设置页
- [x] C3: `app/admin/routes_roster.py`：名册 CSV 导入（类型/大小≤5MB/UTF-8/列头/重复学号校验、预览确认），学号 HMAC 存储，姓名明文仅 admin 可见
- [x] C4: `app/admin/routes_spotcheck.py`：抽查配置页 + 列表页（结果/不一致标记人工）
- [x] C5: `app/workers/spotcheck.py`：周期抽样任务（已判定通过记录复审入 gw_spotcheck_logs，不一致仅标记人工）
- [x] C6: `app/workers/archive.py`：本地归档（周期快照 ai_review.db/gateway.db 至 data/archive/，保留 N 份）
- [x] C7: `app/admin/routes_risk.py`：注册风控视图（被拒/REVIEW 占比、备注模板聚类、通道冻结开关；同 IP/UA 聚合需主仓数据标注二期）
- [x] C8: `app/workers/cleanup.py`：日志保留期清理任务
- [x] C9: `app/admin/templates/settings.html`/`roster.html`/`spotcheck.html`/`risk.html`
- [x] C10: pytest：C1-C9（设置优先级/HMAC/CSV 校验/抽查/保留期）31 用例全绿
- [x] C11: T-C8 压测（test_perf.py）：并发读 + 日志批量写入，结论写入 docs/compose/plans/perf-result.md（SQLite+WAL+busy_timeout 满足规模基线；另修复 CSRF 双提交生产缺陷与名册比对链路 B→C 带入）

验证：

- pytest 全绿
- 压测通过
- 提交

## 交付（最终）

- [ ] D1: `docker-compose.yml` 增加 caddy 服务（Caddyfile 模板）+ 全部服务 `restart: always` + 健康检查自愈
- [ ] D2: `Dockerfile`（已存在，确认基础镜像与依赖一致）
- [ ] D3: `.env.example` 扩展（`ADMIN_SECRET`、`ADMIN_INIT_USER/PASS`、`MIMO_API_KEY` 默认项等）
- [ ] D4: `README.md`「安全部署手册」（ADMIN_SECRET 生成/保管/轮换、端口/防火墙/证书/备份/改密/供应商接入/MiMo 提示）
- [ ] D5: 送审版归档入仓：`docs/submissions/`（含 v3 docx 与 AI 初审核原文）
- [ ] D6: pytest 全套 ≥90 用例全绿
- [ ] D7: 提交推送 feat/web-admin（零 PR，等用户指示）

## Out of Scope（本期不做）

- 学号实名功能门控与未实名功能降级（需主仓配合新字段，二期立项）
- 整套数据全 PostgreSQL 压测（仅压 SQLite 留 PG 切换能力）
- TOTP 强制开启（可选实现）