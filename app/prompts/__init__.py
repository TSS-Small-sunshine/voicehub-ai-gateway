"""VoiceHub AI Gateway — prompts 包（分场景系统提示词）。"""

from .register import REGISTER_SYSTEM_PROMPT
from .song import SONG_SYSTEM_PROMPT
from .note import NOTE_SYSTEM_PROMPT
from .language import LANGUAGE_SYSTEM_PROMPT

__all__ = [
    "REGISTER_SYSTEM_PROMPT",
    "SONG_SYSTEM_PROMPT",
    "NOTE_SYSTEM_PROMPT",
    "LANGUAGE_SYSTEM_PROMPT",
]