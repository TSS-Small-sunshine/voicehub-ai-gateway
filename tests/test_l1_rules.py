"""L1 规则引擎离线测试（真实正则，无需网络/LLM）。"""
import pytest

from app.reviewers.l1_rules import DEFAULT_PATTERNS, L1RulesReviewer


@pytest.fixture
def l1() -> L1RulesReviewer:
    return L1RulesReviewer()


def test_builtin_rules_loaded():
    assert len(DEFAULT_PATTERNS) >= 6


async def test_phone_hit(l1):
    r = await l1.review("联系我 13812345678")
    assert r and r["decision"] == "REJECT" and r["hit"] == "phone_cn"


async def test_qq_hit(l1):
    r = await l1.review("加我 QQ 123456789")
    assert r and r["hit"] == "qq_number"


async def test_wechat_hit(l1):
    r = await l1.review("加微信号 abc_def123")
    assert r and r["hit"] == "wechat_id"


async def test_url_hit(l1):
    r = await l1.review("详见 https://example.com/x")
    assert r and r["hit"] == "url"


async def test_ad_keyword_hit(l1):
    r = await l1.review("加群领福利")
    assert r and r["hit"] == "ad_keywords"


async def test_abuse_hit(l1):
    r = await l1.review("你是傻逼吗")
    assert r and r["hit"] == "abuse"


async def test_clean_text_passes(l1):
    assert await l1.review("今天想点一首晴天送给全班同学") is None


async def test_register_scene_skips_bare_digit_rules(l1):
    # 学号/纯数字用户名在注册场景不触发裸数字规则（防误杀删号）
    assert await l1.review("用户名：2024010101", "register") is None
    assert await l1.review("电话 13812345678", "register") is None


async def test_note_scene_still_hits_bare_digit_rules(l1):
    r = await l1.review("联系 13812345678", "note")
    assert r and r["hit"] == "phone_cn"
    r2 = await l1.review("加 QQ 123456789", "note")
    assert r2 and r2["hit"] == "qq_number"


async def test_register_scene_keeps_wechat_and_abuse_rules(l1):
    assert (await l1.review("加微信号 wxid_abc12345", "register")) is not None
    assert (await l1.review("你是傻逼", "register")) is not None


async def test_empty_text_passes(l1):
    assert await l1.review("") is None
    assert await l1.review(None) is None