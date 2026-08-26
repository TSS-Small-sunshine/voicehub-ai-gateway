"""review_item 编排离线测试：L1 短路 / L2 判定 / replay_note 路由 / 语种分支。"""
import pytest

from app.reviewers.l1_rules import L1RulesReviewer
from app.reviewers.l2_llm import L2LlmReviewer
from app.workers.poll_pending import review_item


class FakeLlm:
    def __init__(self, decision="APPROVE", reason="ok"):
        self._decision = decision
        self._reason = reason

    async def review(self, system_prompt, user_text):
        return {"decision": self._decision, "reason": self._reason, "confidence": 0.9, "source": "l2_llm"}


class FakeLang:
    def __init__(self, result):
        self._result = result

    async def detect(self, title, artist, platform_language=None):
        return self._result


@pytest.fixture
def l1() -> L1RulesReviewer:
    return L1RulesReviewer()


async def test_l1_hit_short_circuits_before_l2(l1):
    item = {"id": 1, "scene": "register", "payload": {"username": "u", "name": "小明", "grade": "高一", "class": "三班", "remark": "加微信 wxid_abc123"}}
    out = await review_item("register", item, l1, FakeLlm(), None)
    assert out["decision"] == "REJECT" and out["source"] == "l1_rules"


async def test_l2_decision_passes_through(l1):
    item = {"id": 2, "scene": "note", "payload": {"text": "同学生日快乐"}}
    out = await review_item("note", item, l1, FakeLlm("APPROVE"), None)
    assert out["decision"] == "APPROVE" and out["source"] == "l2_llm"


async def test_replay_note_item_keeps_its_scene(l1):
    # note 池内重播申请：主仓标注 scene=replay_note，写回必须带该场景
    item = {"id": 5, "scene": "replay_note", "payload": {"songId": 3, "text": "再放一遍"}}
    out = await review_item("note", item, l1, FakeLlm("APPROVE"), None)
    assert out["scene"] == "replay_note" and out["targetId"] == 5


async def test_song_scene_uses_song_payload(l1):
    item = {"id": 6, "scene": "song", "payload": {"title": "晴天", "artist": "周杰伦", "remark": "点给全班"}}
    seen = {}

    class TrackingLlm:
        async def review(self, system_prompt, user_text):
            seen["text"] = user_text
            return {"decision": "APPROVE", "reason": "ok", "confidence": 0.9, "source": "l2_llm"}

    out = await review_item("song", item, l1, TrackingLlm(), None)
    assert out["decision"] == "APPROVE"
    assert "晴天" in seen["text"]


async def test_language_detected_approves(l1):
    item = {"id": 9, "scene": "language", "payload": {"title": "Lemon", "artist": "米津玄師"}}
    lang = FakeLang({"language": "日文", "confidence": 0.9, "source": "l2_llm"})
    out = await review_item("language", item, l1, None, lang)
    assert out["decision"] == "APPROVE" and "日文" in out["reason"]


async def test_language_undetected_reviews(l1):
    item = {"id": 10, "scene": "language", "payload": {"title": "X", "artist": "Y"}}
    lang = FakeLang({"language": "", "confidence": 0.0, "source": "unknown"})
    out = await review_item("language", item, l1, None, lang)
    assert out["decision"] == "REVIEW"