"""NapCat 内置能力：公开接入、工具和瞬时媒体读取。"""

from .catalog import action_coverage
from .connection import NapCatError, NapCatHub, NapCatUnavailable
from .media import MediaReader
from .tools import build_tools

__all__ = [
    "NapCatHub",
    "NapCatError",
    "NapCatUnavailable",
    "MediaReader",
    "build_tools",
    "action_coverage",
]
