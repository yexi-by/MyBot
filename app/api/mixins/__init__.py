"""Mixin 包

提供 BOTClient 的功能拆分 Mixin 类。
"""

from .account import AccountMixin
from .album import AlbumMixin
from .base import BaseMixin
from .file import FileMixin
from .group import GroupMixin
from .message import MessageMixin
from .system import SystemMixin

__all__ = [
    "BaseMixin",
    "MessageMixin",
    "GroupMixin",
    "FileMixin",
    "AlbumMixin",
    "AccountMixin",
    "SystemMixin",
]
