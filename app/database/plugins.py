"""插件专属 schema 的 session 和 repository 构造边界。"""

import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Final, Protocol

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

PLUGIN_SCHEMA_TOKEN: Final[str] = "plugin"
_PLUGIN_SCHEMA_PREFIX: Final[str] = "plugin_"
_POSTGRESQL_IDENTIFIER_MAX_BYTES: Final[int] = 63
MAX_PLUGIN_ID_LENGTH: Final[int] = (
    _POSTGRESQL_IDENTIFIER_MAX_BYTES - len(_PLUGIN_SCHEMA_PREFIX)
)
_PLUGIN_ID_PATTERN: Final[re.Pattern[str]] = re.compile(r"^[a-z][a-z0-9_]*$")


def validate_plugin_id(plugin_id: str) -> str:
    """校验持久化身份，禁止展示名或 SQL 标识符注入。"""
    if _PLUGIN_ID_PATTERN.fullmatch(plugin_id) is None:
        raise ValueError("plugin_id 必须以小写字母开头，且只能包含 a-z、0-9 和下划线")
    if len(plugin_id) > MAX_PLUGIN_ID_LENGTH:
        raise ValueError(
            f"plugin_id 最长为 {MAX_PLUGIN_ID_LENGTH} 个 ASCII 字符，"
            "否则 PostgreSQL 会截断 schema 名"
        )
    return plugin_id


def plugin_schema_name(plugin_id: str) -> str:
    """由稳定插件 ID 生成独立 PostgreSQL schema 名。"""
    return f"{_PLUGIN_SCHEMA_PREFIX}{validate_plugin_id(plugin_id)}"


class PluginSessionFactory:
    """为单个插件创建短生命周期事务。"""

    def __init__(self, *, engine: AsyncEngine, plugin_id: str) -> None:
        """将 schema 映射和插件身份固定在 factory 中。"""
        self.schema: str = plugin_schema_name(plugin_id)
        plugin_engine = engine.execution_options(
            schema_translate_map={PLUGIN_SCHEMA_TOKEN: self.schema}
        )
        self._session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
            plugin_engine,
            expire_on_commit=False,
        )

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[AsyncSession]:
        """为一次 repository 操作创建 session，异常或取消时回滚。"""
        async with self._session_factory() as session:
            async with session.begin():
                # search_path 只作为未显式标注 schema 的 SQL 保护。
                _ = await session.execute(text(f'SET LOCAL search_path TO "{self.schema}"'))
                yield session


class PluginRepositoryBuilder:
    """只向 Context 暴露已构造的插件 repository。"""

    def __init__(self, *, engine: AsyncEngine) -> None:
        """保留基础设施 engine，不把它交给 Context。"""
        self._engine: AsyncEngine = engine

    def create[RepositoryT](
        self,
        plugin_id: str,
        repository_type: "PluginRepositoryConstructor[RepositoryT]",
    ) -> RepositoryT:
        """使用绑定插件 schema 的 factory 构造 repository。"""
        sessions = PluginSessionFactory(engine=self._engine, plugin_id=plugin_id)
        return repository_type(sessions=sessions)


class PluginRepositoryConstructor[RepositoryT](Protocol):
    """插件 repository 构造器必须明确接收已绑定的 session factory。"""

    def __call__(self, *, sessions: PluginSessionFactory) -> RepositoryT:
        """构造一个不向 Context 暴露 session 的 repository。"""
        ...
