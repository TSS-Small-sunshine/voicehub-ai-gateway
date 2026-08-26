# T-C8 压测结论：SQLite 并发读写基线

日期：2026-08-27 · 环境：Windows 本地开发机，Python 3.14，SQLite via SQLAlchemy
命令：`.venv\Scripts\python -m pytest tests/test_perf.py -q -s`

## 场景与结果

| 指标 | 数值 | 说明 |
|---|---|---|
| 并发写吞吐 | **128 rows/s** | 8 线程 × 250 行逐行 commit，共 2000 行，15.6s |
| 并发读 | **18,565 次** 查询无异常、不阻塞 | 4 读线程贯穿整个写入窗口 |
| 单事务批量 | 2000 行 / **0.24s**（≈8300 rows/s） | worker 日志可改用攒批进一步提速 |
| 错误数 | 0 | 无 `database is locked`、无死锁 |

## 加固项（本分支落地）

初始裸配置（默认 `busy_timeout=0`、journal=DELETE）在 8 并发写下立即出现
`sqlite3.OperationalError: database is locked`。修复：`app/db.py` 统一引擎构造器
`make_engine()`：

1. `connect_args timeout=30` → sqlite busy_timeout，等待持锁方而非立刻抛错；
2. 每连接 PRAGMA `journal_mode=WAL` + `synchronous=NORMAL` → 写不阻塞读。

测试与生产共用该构造路径；fixtures 的临时引擎亦受益。

## 选型结论（SPEC [S9] 承诺兑现）

- **校方规模（≤5 管理员 / 日审 ≤1000）下 SQLite(+WAL+busy_timeout) 完全够用**：
  写路径每审核条目一次 commit，1000 条/日的峰值负载对本基线余量 >10 倍；
- 管理台人工复核并发量小（128 rows/s ≈ 单管理台页面级操作吞吐数百倍于需求）；
- 未来若日审 ×10 或并发复核密集导致锁等待上升，**仅改连接串即可切 PostgreSQL**
  （全链路 SQLAlchemy ORM，无需改代码）；MySQL 同路径。

## 遗留优化位（非本期）

- `_persist_log` 攒批提交（每轮 N 条单事务）可再降 fsync 开销一个数量级；
- 归档任务对 WAL 库已天然一致（backup API 输出独立完整快照文件）。
