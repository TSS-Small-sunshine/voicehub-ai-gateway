"""T-C8 压测：SQLite 并发读写基线（校方规模 ≤5 管理员 / 日审 ≤1000）。

场景：
1) 8 线程并发插入（模拟多管理员同时复核 + worker 写日志），吞吐须 >100 行/s；
2) 读写混跑：读线程持续 COUNT(*)，验证并发读不阻塞、无异常；
3) 单会话批量写入 2000 行耗时（仅记录，作选型参考）。

结果同步记入 docs/compose/plans/perf-result.md。阈值取宽松下限防环境抖动。
"""
import os
import tempfile
import threading
import time

os.environ.setdefault("ADMIN_SECRET", "test-secret-1234567890abcdef0123456789abcd")

_ROWS_PER_THREAD = 250
_N_WRITERS = 8


def _seed_engine():
    """与生产同一路径的引擎（WAL + busy_timeout 加固，见 app.db.make_engine）。"""
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    from app.db import AiReviewLog, Base, make_engine

    engine = make_engine(f"sqlite:///{path}")
    Base.metadata.create_all(bind=engine, tables=[AiReviewLog.__table__])
    return engine, path


def test_perf_concurrent_writes_and_reads():
    from app.db import AiReviewLog

    engine, path = _seed_engine()
    errors: list[BaseException] = []

    def writer(tid: int):
        Sm = sessionmaker_local(engine)
        try:
            session = Sm()
            for i in range(_ROWS_PER_THREAD):
                session.add(AiReviewLog(
                    scene="note", target_id=tid * 10000 + i, decision="APPROVE",
                    reason="perf", confidence=0.9, model="l2_llm", source="l2_llm",
                    duration_ms=5, payload_json='{"text":"压测"}',
                ))
                session.commit()
            session.close()
        except BaseException as e:  # noqa: BLE001 收集到主线程断言
            errors.append(e)

    sessionmaker_local = __import__("sqlalchemy").orm.sessionmaker

    read_stop = threading.Event()
    read_iters = {"n": 0}

    def reader():
        Sm = sessionmaker_local(engine)
        while not read_stop.is_set():
            try:
                s = Sm()
                s.query(AiReviewLog.id).limit(100).all()
                s.close()
                read_iters["n"] += 1
            except BaseException as e:  # noqa: BLE001
                errors.append(e)
                return

    readers = [threading.Thread(target=reader, daemon=True) for _ in range(4)]
    writers = [threading.Thread(target=writer, args=(t,)) for t in range(_N_WRITERS)]

    t0 = time.perf_counter()
    for r in readers:
        r.start()
    for w in writers:
        w.start()
    for w in writers:
        w.join(timeout=60)
    elapsed = time.perf_counter() - t0
    read_stop.set()
    for r in readers:
        r.join(timeout=5)

    total_rows = _N_WRITERS * _ROWS_PER_THREAD
    throughput = total_rows / max(elapsed, 1e-6)

    # 批量单事务写入耗时（参考值）
    Sm = sessionmaker_local(engine)
    bulk_t0 = time.perf_counter()
    s = Sm()
    try:
        s.add_all([
            AiReviewLog(scene="register", target_id=i, decision="APPROVE", payload_json="{}")
            for i in range(2000)
        ])
        s.commit()
        bulk_seconds = time.perf_counter() - bulk_t0
    finally:
        s.close()

    engine.dispose()
    try:
        os.unlink(path)
    except OSError:
        pass

    print(f"[perf] {total_rows} rows via {_N_WRITERS} threads in {elapsed:.2f}s "
          f"→ {throughput:.0f} rows/s; readers completed {read_iters['n']} queries; "
          f"bulk-2000 single-commit {bulk_seconds:.2f}s")

    assert not errors, f"concurrent access raised: {errors[:3]}"
    assert throughput > 100, f"insert throughput too low: {throughput:.1f} rows/s"
    assert read_iters["n"] > 50, "readers barely progressed — concurrency broken"
