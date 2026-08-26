# VoiceHub AI Gateway · AI 初审导读

> 本文档是给「审核 AI」的完整上下文包：读完即可建立全局理解、按清单核查、自行运行验证。
> 维护约定：代码行为变化时同步更新本文件；审核 AI 对本文件的疑问优先级高于口头描述。

## 1. 系统一句话 + 你的任务

**VoiceHub AI Gateway** 是校园广播点歌系统（VoiceHub）的外置 AI 审核网关：从主仓拉待审数据 → L1 规则 → L2 LLM → L3 搜索 三级漏斗判定 → 写回；附带 Web 管理台（认证/队列人工复核/日志/规则/供应商/名册比对/抽查/风控/设置热生效）。数据全部本地存储，学生 PII 出站前强制脱敏。

**你的任务**：按 §6 审核清单对实现做初步审核，输出分级发现（critical/major/minor），每条带 `file:line` 证据。不需要信任任何未验证声明——§5 的证据均可自行重跑复核。

## 2. 仓库地图（关键文件速查）

| 路径 | 职责 | 审核关注点 |
|---|---|---|
| `app/main.py` | FastAPI 入口、lifespan 挂载轮询+抽查+归档+清理四后台任务、路由注册 | 后台任务取消是否干净 |
| `app/config.py` | pydantic-settings，环境变量定义 | — |
| `app/settings.py` | 运行期设置：`gw_settings` DB > env > 默认；快照 `get_settings()` | 键必须在 DEFAULTS 登记防任意写；空值=清除覆盖 |
| `app/db.py` | 双库 ORM（ai_review.db 日志 / gateway.db 管理台）、`make_engine()` WAL+busy_timeout 加固 | 引擎 monkeypatch 兼容测试 |
| `app/security.py` | argon2 密码 / Fernet(API Key) / HMAC-SHA256(学号) / TOTP / 会话 token | 根密钥全部来自 `ADMIN_SECRET` |
| `app/auth.py` | 用户/会话/锁定/CSRF | CSRF 为双提交常量时间比较（有状态会话，无 itsdangerous 二次签名——历史上曾有签名误用导致全部表单 400 的缺陷，已修，见 commit 0896ebe） |
| `app/mask.py` | PII 脱敏（姓名/手机/QQ/学号/身份证/URL） | 所有出站与落盘路径必经 |
| `app/admin/decorators.py` | login_required / require_role / csrf_protect | 角色矩阵：admin 全部；reviewer 队列·日志·规则·抽查；viewer 只读脱敏视图；设置/名册/风控/供应商仅 admin |
| `app/workers/poll_pending.py` | 每轮读设置快照→拉待审→三级漏斗→质量门→写回 | 异常一律降级 REVIEW 不丢单；REVIEW 冷却去重；冻结开关剔除 register |
| `app/roster.py` | 名册 CSV 解析/HMAC 导入幂等/备注比对 | 校验链见 §4-5；比对**绝不自动写回**，只转 REVIEW |
| `app/workers/spotcheck.py` | 周期复审已通过记录→gw_spotcheck_logs | REJECT 仅标记待人工 |
| `app/workers/archive.py` | SQLite backup API 快照至 data/archive/ 按 stem 分组保留 N 份 | 非 SQLite 数据源跳过 |
| `app/workers/cleanup.py` | 按保留期分批删过期日志 | 默认 180 天 |
| `app/providers/` + `app/l2_llm.py` | OpenAI 兼容供应商 registry/service/Fernet、主备熔断切换 | env Key 与库内加密 Key 并存，env 优先 |
| `tests/` | 全离线 pytest（fake LLM/搜索，不触网） | 压测 test_perf.py 可独立跑 |

## 3. 数据流（出站仅两条箭头带脱敏）

```
浏览器 ──HTTPS(443,Caddy唯一入口)──► FastAPI
                                      ├─ /admin/* 管理台（登录+角色+CSRF）
                                      ├─ /health 探活
                                      └─ 轮询worker ─► 主仓 /api/open/*（X-API-Key）
                                                    ─► LLM/Tavily（送审文本先经 app/mask.py 脱敏）
本地落盘：data/ai_review.db（日志,已脱敏） + data/gateway.db（用户/会话/规则/供应商[Fernet]/名册[学号HMAC]/审计/抽查）
本地归档：data/archive/*.db（周期快照，仅留本地）
```

## 4. 安全设计决策（每条可指认代码）

1. **强制脱敏**：送 LLM/L3 文本与 `payload_json` 落盘均过 `mask.py`（poll_pending.py 构造路径 + routes_logs viewer 渲染）。
2. **密钥体系单根**：`ADMIN_SECRET` 派生 Fernet key（供应商 API Key 加密）与学号 HMAC；未配置时相关功能显式抛 RuntimeError 拒绝明文入库（security.py:55-81）。
3. **CSRF 双提交**：会话行存裸随机 token，模板原样注入表单隐藏域，提交时常量时间比较（auth.py:csrf_check）；Cookie HttpOnly+Secure+SameSite=Strict。
4. **登录限流**：失败 5 次→锁 15 分钟（auth.py:MAX_FAILED/LOCK_DURATION）。
5. **SQLite 并发加固**：`make_engine()` 统一 busy_timeout=30s + journal_mode=WAL + synchronous=NORMAL（db.py）。
6. **角色最小权限**：viewer 仅脱敏视图；高危页 admin-only（装饰器矩阵，decorators.py:require_role 各路由挂点）。
7. **审计只追加**：管理操作全量入 gw_audit_logs 含 before/after JSON 与 IP。
8. **不自动处置学生账号**：人工写回需 reviewer/admin 显式操作且 reason 强制「人工」前缀；抽查不一致仅标记。

## 5. 已验证证据（可复跑）

```bash
# 全套离线测试（预期 110 passed）
.venv\Scripts\python -m pytest -q
# 压测单独跑（观察 [perf] 行）
.venv\Scripts\python -m pytest tests/test_perf.py -q -s
```

基线记录：110 passed / 0 failed；并发压测 8 写线程 128 rows/s、18,565 次并发读零锁错误、批量 2000 单事务 0.24s（详见 docs/compose/plans/perf-result.md）。分支 feat/web-admin，A-C 累计范围 `1e99551..d28048f`。

## 6. 审核清单（建议顺序）

1. 读本文档 §2 表格右列关注点，逐文件 grep 关键词抽查；
2. 安全敏感路径精读：auth.py → decorators.py → security.py → roster.py(CSV 边界/HMAC/比对分支) → poll_pending.py(配置消费点是否仍有直读 settings 的运行期参数遗漏)；
3. 一致性检查：新增路由渲染是否都传了 base.html 所需的 `user`；惰性导入 db 会话是否会被测试 fixture 替换机制覆盖；
4. 测试有效性：抽 3 个 C 阶段用例，确认断言的是行为而非实现细节，无恒真断言；
5. 自行跑 §5 命令复核。

### 已知边界（防止误报的 Out of Scope）

- 学号实名功能门控、IP/UA 聚合风控：二期（需主仓配合）；
- 非 OpenAI 兼容供应商 adapter：预留接口未实现；
- 抽查绝不自动删号/写回；落盘不做整库加密（决策见 spec [S13]）；
- 同 IP 登录限流之外的反爬/CC：Caddy 层职责。

## 7. 需求源文档

- 设计规格：docs/compose/specs/2026-08-26-web-admin-design.md（[S1]-[S13]）
- 实施计划与勾选状态：docs/compose/plans/2026-08-27-web-admin-plan.md
- 压测结论：docs/compose/plans/perf-result.md
