"""VoiceHub AI Gateway — 后台轮询 Worker。

流程：拉待审 → L1 规则（REJECT/放行）→ L2 LLM →（语种场景可选 L3）→ 写回主仓库。
任何异常 → 该条记 REVIEW 写回（保持 pending 转人工，绝不丢单）。
"""
import asyncio
import json
import logging
import time

from ..config import settings
from ..db import AiReviewLog, SessionLocal, init_db
from ..prompts import NOTE_SYSTEM_PROMPT, REGISTER_SYSTEM_PROMPT, SONG_SYSTEM_PROMPT
from ..reviewers import L2LlmReviewer, L1RulesReviewer, L3SearchReviewer
from ..reviewers.language_detector import LanguageDetector
from ..voicehub_client import VoiceHubClient

log = logging.getLogger("ai-gateway")


def get_scenes() -> list[str]:
    """当前启用的轮询场景（每轮读取，支持运行期配置变更）。

    REVIEW_SCENES 逗号分隔；song/language 需主仓 Phase 3 状态机支持后开启。
    """
    return [s.strip() for s in settings.review_scenes.split(",") if s.strip()] or ["register", "note"]

# 各场景送审文本构造（从主仓库 payload 提取）
def build_review_text(scene: str, payload: dict) -> str:
    p = payload or {}
    if scene == "register":
        return (
            f"用户名：{p.get('username','')}\n姓名：{p.get('name','')}\n"
            f"选择年级：{p.get('grade','')} 班级：{p.get('class','')}\n备注：{p.get('remark','')}"
        )
    if scene == "song":
        return f"标题：{p.get('title','')}\n歌手：{p.get('artist','')}\n备注：{p.get('remark','')}"
    if scene == "note" or scene == "replay_note":
        return f"留言：{p.get('text','')}"
    if scene == "language":
        return f"标题：{p.get('title','')}\n歌手：{p.get('artist','')}"
    return ""


# L1 只扫自由文本（注册场景不含下拉选择的年级/班级，防年份类数字误伤规则）
def build_l1_text(scene: str, payload: dict) -> str:
    p = payload or {}
    if scene == "register":
        return (
            f"用户名：{p.get('username','')}\n姓名：{p.get('name','')}\n备注：{p.get('remark','')}"
        )
    return build_review_text(scene, payload)


def build_system_prompt(scene: str) -> str:
    if scene == "register":
        return REGISTER_SYSTEM_PROMPT
    if scene == "song":
        return SONG_SYSTEM_PROMPT
    if scene in ("note", "replay_note"):
        return NOTE_SYSTEM_PROMPT
    return ""


async def review_item(scene: str, item: dict, l1: L1RulesReviewer, l2: L2LlmReviewer, lang: LanguageDetector) -> dict:
    """单条审核：返回写回结果。

    scene 以主仓 pending-list 在 item 上的标注为准（note 池内的重播申请标注为
    replay_note，写回须走对应分支）。
    """
    target_id = item.get("id")
    payload = item.get("payload") or {}
    item_scene = item.get("scene") or scene
    start = time.monotonic()

    if item_scene == "language":
        # 语种专用：平台元数据 → LLM → 搜索
        result = await lang.detect(
            title=str(payload.get("title", "")),
            artist=str(payload.get("artist", "")),
            platform_language=cfg_platform_language(payload),
        )
        duration_ms = int((time.monotonic() - start) * 1000)
        if not result.get("language"):
            return _result(item_scene, target_id, "REVIEW", "无法识别语种，转人工", None, result.get("source"), duration_ms, payload)
        return _result(item_scene, target_id, "APPROVE", f"语种：{result['language']}", result.get("confidence"), result.get("source"), duration_ms, payload)

    text = build_review_text(item_scene, payload)

    # L1（注册只扫自由文本；scene 传参与规则的 skip_scenes 匹配）
    l1_result = await l1.review(build_l1_text(item_scene, payload), item_scene)
    if l1_result:
        return _result(item_scene, target_id, l1_result["decision"], l1_result["reason"], 1.0, l1_result["source"], int((time.monotonic() - start) * 1000), payload)

    # L2（注册场景带专用 prompt）
    result = await l2.review(build_system_prompt(item_scene), text)
    duration_ms = int((time.monotonic() - start) * 1000)
    return _result(item_scene, target_id, result["decision"], result["reason"], result.get("confidence"), result.get("source"), duration_ms, payload)


def cfg_platform_language(payload: dict) -> str | None:
    """L1 平台元数据（主仓 payload.language）。"""
    lang = payload.get("language")
    return str(lang) if lang else None


def _result(scene, target_id, decision, reason, confidence, source, duration_ms, payload) -> dict:
    return {
        "scene": scene,
        "targetId": target_id,
        "decision": decision,
        "reason": reason,
        "confidence": confidence,
        "source": source,
        "durationMs": duration_ms,
        "payloadJson": json.dumps(payload, ensure_ascii=False),
    }


async def poll_once(client: VoiceHubClient, l1, l2, lang, state: dict | None = None) -> None:
    """单轮：遍历各场景拉待审 → 审核 → 写回 → 记本地日志。

    state：{(scene, targetId): 最近 REVIEW 提交时刻}，冷却期内跳过重审，
    防止 song/language 等 no-op 场景与 REVIEW 项每轮重复调 LLM。
    """
    if state is None:
        state = {}
    now = time.monotonic()
    for scene in get_scenes():
        try:
            items = await client.fetch_pending(scene, limit=settings.poll_batch_size)
        except Exception as e:
            log.warning("拉取 %s 待审失败: %s", scene, e)
            continue
        for item in items:
            key = (item.get("scene") or scene, item.get("id"))
            last = state.get(key)
            if last is not None and now - last < settings.review_cooldown_seconds:
                continue
            try:
                result = await review_item(scene, item, l1, l2, lang)
            except Exception as e:
                # 单条异常降级 REVIEW 转人工，绝不中断整轮轮询
                log.warning("审核 %s#%s 异常，降级 REVIEW: %s", scene, item.get("id"), e)
                result = {
                    "scene": item.get("scene") or scene,
                    "targetId": item.get("id"),
                    "decision": "REVIEW",
                    "reason": f"审核异常（{type(e).__name__}），转人工",
                    "confidence": None,
                    "source": "degraded",
                    "durationMs": 0,
                    "payloadJson": json.dumps(item.get("payload") or {}, ensure_ascii=False),
                }
            _persist_log(result)
            try:
                await client.submit_result(
                    scene=result["scene"],
                    target_id=result["targetId"],
                    decision=result["decision"],
                    reason=result["reason"],
                    confidence=result["confidence"],
                    model=result.get("source"),
                    source=result.get("source"),
                    duration_ms=result.get("durationMs"),
                )
            except Exception as e:
                log.warning("写回 %s#%s 失败: %s", scene, result["targetId"], e)
                continue
            # 写回成功后记录 REVIEW 时刻（冷却期不重审）；写回失败不记录，下一轮重试
            if result["decision"] == "REVIEW":
                state[key] = time.monotonic()


def _persist_log(result: dict) -> None:
    try:
        session = SessionLocal()
        try:
            session.add(
                AiReviewLog(
                    scene=result["scene"],
                    target_id=result["targetId"],
                    decision=result["decision"],
                    reason=(result.get("reason") or "")[:2000],
                    confidence=result.get("confidence"),
                    model=result.get("source"),
                    source=result.get("source"),
                    duration_ms=result.get("durationMs"),
                    payload_json=(result.get("payloadJson") or "")[:4000],
                )
            )
            session.commit()
        finally:
            session.close()
    except Exception as e:
        log.error("本地审核日志写入失败: %s", e)


async def run_poll_loop() -> None:
    """后台常驻轮询循环（lifespan 启动）。"""
    init_db()
    if not settings.voicehub_api_key or not settings.voicehub_api_base_url:
        log.warning("未配置 VOICEHUB_API_KEY/BASE_URL，轮询停用")
        return
    client = VoiceHubClient()
    l1 = L1RulesReviewer()
    l2 = L2LlmReviewer()
    search = L3SearchReviewer()
    lang = LanguageDetector(l2, search)
    if not settings.llm_api_key:
        log.warning("未配置 LLM Key：仅 L1 规则生效，其余判定恒 REVIEW")
    log.info("AI 网关轮询启动，间隔 %ss，场景 %s", settings.poll_interval_seconds, ",".join(get_scenes()))
    state: dict = {}
    try:
        while True:
            await poll_once(client, l1, l2, lang, state)
            await asyncio.sleep(settings.poll_interval_seconds)
    except asyncio.CancelledError:
        log.info("轮询停止")
    finally:
        await client.close()