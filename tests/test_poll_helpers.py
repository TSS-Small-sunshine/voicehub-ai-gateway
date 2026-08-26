"""poll_pending 纯函数离线测试（不触网、不启动轮询）。"""
import pytest

from app.prompts import NOTE_SYSTEM_PROMPT, REGISTER_SYSTEM_PROMPT, SONG_SYSTEM_PROMPT
from app.workers.poll_pending import (
    _result,
    build_l1_text,
    build_review_text,
    build_system_prompt,
    cfg_platform_language,
)


def test_build_review_text_register():
    text = build_review_text(
        "register",
        {"username": "u1", "name": "小明", "grade": "高一", "class": "三班", "remark": "请多关照"},
    )
    assert "高一" in text and "三班" in text and "小明" in text


def test_build_review_text_song():
    text = build_review_text("song", {"title": "晴天", "artist": "周杰伦", "remark": "想点"})
    assert "晴天" in text and "周杰伦" in text


def test_build_review_text_note_and_replay_note():
    n = build_review_text("note", {"text": "生日快乐"})
    r = build_review_text("replay_note", {"songId": 3, "text": "再来一遍"})
    assert n == "留言：生日快乐"
    assert r == "留言：再来一遍"


def test_build_review_text_language():
    text = build_review_text("language", {"title": "Lemon", "artist": "米津玄師"})
    assert "Lemon" in text and "米津玄師" in text


def test_build_l1_text_register_excludes_grade():
    text = build_l1_text(
        "register",
        {"username": "u1", "name": "小明", "grade": "2026级", "class": "三班", "remark": "低调"},
    )
    assert "2026级" not in text and "低调" in text


def test_build_l1_text_note_equals_review_text():
    p = {"title": "晴天", "artist": "周杰伦", "text": "点一首"}
    assert build_l1_text("note", p) == build_review_text("note", p)


def test_build_system_prompt_scenes():
    assert build_system_prompt("register") == REGISTER_SYSTEM_PROMPT
    assert build_system_prompt("song") == SONG_SYSTEM_PROMPT
    assert build_system_prompt("note") == NOTE_SYSTEM_PROMPT
    assert build_system_prompt("replay_note") == NOTE_SYSTEM_PROMPT
    assert build_system_prompt("unknown_scene") == ""


def test_cfg_platform_language():
    assert cfg_platform_language({"language": "中文"}) == "中文"
    assert cfg_platform_language({"language": None}) is None
    assert cfg_platform_language({}) is None


def test_result_builder_shape():
    r = _result("note", 7, "REJECT", "违规", 0.9, "l1_rules", 12, {"text": "哈哈"})
    assert r["scene"] == "note" and r["targetId"] == 7 and r["decision"] == "REJECT"
    assert '"text": "哈哈"' in r["payloadJson"]