"""L2 LLM 离线测试（fake OpenAI 客户端 / monkeypatch _call_json，不触网）。"""
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.config import settings
from app.reviewers.l2_llm import L2LlmReviewer, build_messages


def _reviewer_with_client(content: str) -> L2LlmReviewer:
    r = L2LlmReviewer()
    r._client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=AsyncMock(
                    return_value=SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=content))]
                    )
                )
            )
        )
    )
    return r


async def test_parse_json_plain():
    r = _reviewer_with_client('{"decision":"APPROVE","reason":"合规","confidence":0.9}')
    data = await r._call_json("sys", "user")
    assert data == {"decision": "APPROVE", "reason": "合规", "confidence": 0.9}


async def test_parse_json_fenced():
    r = _reviewer_with_client('```json\n{"decision":"REJECT","reason":"广告"}\n```')
    data = await r._call_json("sys", "user")
    assert data["decision"] == "REJECT"


async def test_parse_json_garbage_is_none():
    r = _reviewer_with_client("这不是 JSON")
    assert await r._call_json("sys", "user") is None


def test_build_messages_shape():
    msgs = build_messages("S", "U")
    assert [m["role"] for m in msgs] == ["system", "user"]


async def test_review_valid(monkeypatch):
    r = L2LlmReviewer()

    async def fake(system_prompt, user_text):
        return {"decision": "APPROVE", "reason": "ok", "confidence": 0.8}

    monkeypatch.setattr(r, "_call_json", fake)
    out = await r.review("sys", "user")
    assert out["decision"] == "APPROVE"
    assert out["confidence"] == 0.8
    assert out["source"] == "l2_llm"
    assert out["model"] == settings.llm_model


async def test_review_invalid_decision_falls_back(monkeypatch):
    r = L2LlmReviewer()

    async def fake(_s, _u):
        return {"decision": "MAYBE"}

    monkeypatch.setattr(r, "_call_json", fake)
    out = await r.review("sys", "user")
    assert out["decision"] == "REVIEW"
    assert "无法解析" in out["reason"]


async def test_review_exception_falls_back(monkeypatch):
    r = L2LlmReviewer()

    async def fake(_s, _u):
        raise TimeoutError("network")

    monkeypatch.setattr(r, "_call_json", fake)
    out = await r.review("sys", "user")
    assert out["decision"] == "REVIEW"
    assert "调用失败" in out["reason"]
    assert out["confidence"] == 0.0


async def test_complete_json_returns_none_on_error(monkeypatch):
    r = L2LlmReviewer()

    async def fake(_s, _u):
        raise ValueError("boom")

    monkeypatch.setattr(r, "_call_json", fake)
    assert await r.complete_json("sys", "user") is None