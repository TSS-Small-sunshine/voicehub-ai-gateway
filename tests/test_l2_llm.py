"""L2 LLM 离线测试（mock l2_llm.call_json，不触网）。"""
import pytest

from app.config import settings
from app.reviewers.l2_llm import L2LlmReviewer, build_messages


@pytest.fixture
def mock_call_json(monkeypatch):
    """monkeypatch 字符串路径在 pytest-asyncio 下偶发失效：直接传 module 对象 + 验证。"""
    from app import llm_client, reviewers

    def _set(fake):
        # 同时替换源头与 l2_llm 模块本地引用（from import 已绑定）
        monkeypatch.setattr(llm_client, "call_json", fake)
        monkeypatch.setattr(reviewers.l2_llm, "call_json", fake)

    return _set


async def test_call_json_parses_plain(mock_call_json):
    async def fake(system_prompt, user_text):
        return {"content": '{"decision":"APPROVE","reason":"合规","confidence":0.9}', "model": "x", "provider": "p"}
    mock_call_json(fake)
    r = L2LlmReviewer()
    data = await r._call_json("sys", "user")
    assert data == {"decision": "APPROVE", "reason": "合规", "confidence": 0.9}


async def test_call_json_parses_fenced(mock_call_json):
    """验证 l2_llm._call_json 能解析剥离 fence 后的 JSON。"""
    async def fake(system_prompt, user_text):
        return {"content": '{"decision":"REJECT","reason":"广告"}', "model": "x", "provider": "p"}
    mock_call_json(fake)
    r = L2LlmReviewer()
    data = await r._call_json("sys", "user")
    assert data["decision"] == "REJECT"


async def test_call_json_garbage_is_none(mock_call_json):
    async def fake(system_prompt, user_text):
        return {"content": "这不是 JSON", "model": "x", "provider": "p"}
    mock_call_json(fake)
    r = L2LlmReviewer()
    assert await r._call_json("sys", "user") is None


async def test_call_json_returns_none_when_provider_unavailable(mock_call_json):
    async def fake(system_prompt, user_text):
        return None
    mock_call_json(fake)
    r = L2LlmReviewer()
    assert await r._call_json("sys", "user") is None


async def test_call_json_returns_none_on_provider_exception(mock_call_json):
    async def fake(system_prompt, user_text):
        raise TimeoutError("network")
    mock_call_json(fake)
    r = L2LlmReviewer()
    assert await r._call_json("sys", "user") is None


def test_build_messages_shape():
    msgs = build_messages("S", "U")
    assert [m["role"] for m in msgs] == ["system", "user"]


async def test_review_valid(mock_call_json):
    async def fake(system_prompt, user_text):
        return {"content": '{"decision":"APPROVE","reason":"ok","confidence":0.8}', "model": "m", "provider": "p"}
    mock_call_json(fake)
    r = L2LlmReviewer()
    out = await r.review("sys", "user")
    assert out["decision"] == "APPROVE"
    assert out["confidence"] == 0.8
    assert out["source"] == "l2_llm"
    assert out["model"] == settings.llm_model


async def test_review_invalid_decision_falls_back(mock_call_json):
    async def fake(system_prompt, user_text):
        return {"content": '{"decision":"MAYBE"}', "model": "m", "provider": "p"}
    mock_call_json(fake)
    r = L2LlmReviewer()
    out = await r.review("sys", "user")
    assert out["decision"] == "REVIEW"
    assert "无法解析" in out["reason"]


async def test_review_provider_unavailable_falls_back(mock_call_json):
    async def fake(system_prompt, user_text):
        return None
    mock_call_json(fake)
    r = L2LlmReviewer()
    out = await r.review("sys", "user")
    assert out["decision"] == "REVIEW"


async def test_complete_json_returns_none_on_error(mock_call_json):
    async def fake(system_prompt, user_text):
        raise ValueError("boom")
    mock_call_json(fake)
    r = L2LlmReviewer()
    assert await r.complete_json("sys", "user") is None