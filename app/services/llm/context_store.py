"""进程内对话上下文存储。"""

import asyncio
from dataclasses import dataclass

from .context_handler import ContextHandler


@dataclass(frozen=True, slots=True)
class ConversationContextKey:
    """标识一个插件、机器人和会话共同拥有的对话上下文。"""

    owner: str
    bot_id: str
    conversation_id: str


class ConversationContextStore:
    """在当前进程内保存跨连接复用的对话上下文与串行锁。"""

    def __init__(self) -> None:
        """初始化空上下文和锁映射。"""
        self._contexts: dict[ConversationContextKey, ContextHandler] = {}
        self._locks: dict[ConversationContextKey, asyncio.Lock] = {}

    def get(self, *, key: ConversationContextKey) -> ContextHandler | None:
        """读取指定对话上下文。"""
        return self._contexts.get(key)

    def set(
        self, *, key: ConversationContextKey, context: ContextHandler
    ) -> None:
        """保存指定对话上下文。"""
        self._contexts[key] = context

    def remove(self, *, key: ConversationContextKey) -> None:
        """删除指定对话上下文，串行锁继续服务同一进程内的后续请求。"""
        self._contexts.pop(key, None)

    def items_for_owner(
        self, *, owner: str
    ) -> tuple[tuple[ConversationContextKey, ContextHandler], ...]:
        """返回指定所有者当前保存的上下文快照。"""
        return tuple(
            (key, context)
            for key, context in self._contexts.items()
            if key.owner == owner
        )

    def lock_for(self, *, key: ConversationContextKey) -> asyncio.Lock:
        """返回指定对话在当前进程内共享的串行锁。"""
        lock = self._locks.get(key)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[key] = lock
        return lock
