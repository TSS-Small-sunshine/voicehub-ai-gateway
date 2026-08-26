"""poll_once 轮询健壮性：单条异常降级 REVIEW、拉取失败不中断整轮、REVIEW 冷却去重。"""
import pytest

from app.config import settings
from app.reviewers.l1_rules import L1RulesReviewer
from app.workers.poll_pending import poll_once


class SceneClient:
    """按场景返回待审项 + 记录提交。"""

    def __init__(self, scenes: dict):
        self._scenes = scenes
        self.submitted = []

    async def fetch_pending(self, scene: str, limit: int = 20):
        return self._scenes.get(scene, [])

    async def submit_result(self, **kw):
        self.submitted.append(kw)


class BoomReviewer:
    async def review(self, *_a, **_kw):
        raise RuntimeError("boom")


class BoomLang:
    async def detect(self, *_a, **_kw):
        raise RuntimeError("boom")


async def test_single_item_exception_degrades_to_review():
    client = SceneClient({"register": [{"id": 1, "scene": "register", "payload": {"username": "u", "remark": "x"}}]})
    await poll_once(client, BoomReviewer(), None, None)
    assert len(client.submitted) == 1
    assert client.submitted[0]["decision"] == "REVIEW"
    assert client.submitted[0]["target_id"] == 1
    assert client.submitted[0]["source"] == "degraded"


async def test_language_exception_degrades_to_review(monkeypatch):
    monkeypatch.setattr(settings, "review_scenes", "language")
    client = SceneClient({"language": [{"id": 9, "scene": "language", "payload": {"title": "X"}}]})
    # language 分支在 L1 之前调用 lang.detect，异常同样降级 REVIEW
    await poll_once(client, None, None, BoomLang())
    assert len(client.submitted) == 1
    assert client.submitted[0]["decision"] == "REVIEW"


async def test_fetch_failure_skips_scene_not_whole_round():
    class FlakyClient(SceneClient):
        async def fetch_pending(self, scene: str, limit: int = 20):
            if scene == "note":
                raise ConnectionError("down")
            return self._scenes.get(scene, [])

    client = FlakyClient({"register": [{"id": 1, "scene": "register", "payload": {"username": "u", "remark": "x"}}]})

    class OkReviewer:
        async def review(self, text, *_a, **_kw):
            return {"decision": "APPROVE", "reason": "ok", "confidence": 0.9, "source": "l2_llm"}

    await poll_once(client, OkReviewer(), None, None)  # 不应抛异常
    # note 拉取失败被跳过，register 照常处理
    assert len(client.submitted) == 1


async def test_writeback_failure_keeps_local_log_and_continues():
    client = SceneClient({"register": [{"id": 1, "scene": "register", "payload": {"username": "u", "remark": "x"}}]})

    class FailSubmitClient(SceneClient):
        async def submit_result(self, **kw):
            raise ConnectionError("writeback down")

    class OkReviewer:
        async def review(self, text, *_a, **_kw):
            return {"decision": "APPROVE", "reason": "ok", "confidence": 0.9, "source": "l2_llm"}

    c = FailSubmitClient({"register": [{"id": 1, "scene": "register", "payload": {"username": "u", "remark": "x"}}]})
    await poll_once(c, OkReviewer(), None, None)  # 写回失败仅告警，不中断


async def test_review_cooldown_skips_same_item_next_round(monkeypatch):
    monkeypatch.setattr(settings, "review_cooldown_seconds", 300)
    item = {"id": 1, "scene": "note", "payload": {"text": "同学生日快乐"}}

    class NoteClient(SceneClient):
        async def fetch_pending(self, scene, limit=20):
            return [item] if scene == "note" else []

    class ReviewLlm:
        async def review(self, system_prompt, user_text):
            return {"decision": "REVIEW", "reason": "转人工确认", "confidence": 0.4, "source": "l2_llm"}

    client = NoteClient({})
    state = {}
    await poll_once(client, L1RulesReviewer(), ReviewLlm(), None, state)
    assert len(client.submitted) == 1 and client.submitted[0]["decision"] == "REVIEW"
    # 第二轮的同一待审项在冷却期内被跳过，不重复调 LLM/写回
    await poll_once(client, L1RulesReviewer(), ReviewLlm(), None, state)
    assert len(client.submitted) == 1


async def test_writeback_failure_does_not_set_cooldown(monkeypatch):
    monkeypatch.setattr(settings, "review_cooldown_seconds", 300)

    class FailSubmitClient(SceneClient):
        async def submit_result(self, **kw):
            raise ConnectionError("writeback down")

    class ReviewLlm:
        async def review(self, system_prompt, user_text):
            return {"decision": "REVIEW", "reason": "转人工", "confidence": 0.4, "source": "l2_llm"}

    item = {"id": 2, "scene": "note", "payload": {"text": "再放一遍"}}
    c = FailSubmitClient({"note": [item]})
    state = {}
    await poll_once(c, L1RulesReviewer(), ReviewLlm(), None, state)
    assert len(c.submitted) == 0  # 写回失败：不记录冷却，下一轮重试


async def test_scenes_read_from_config_each_round(monkeypatch):
    monkeypatch.setattr(settings, "review_scenes", "register")
    fetched = []

    class TrackClient(SceneClient):
        async def fetch_pending(self, scene, limit=20):
            fetched.append(scene)
            return []

    await poll_once(TrackClient({}), None, None, None)
    assert fetched == ["register"]