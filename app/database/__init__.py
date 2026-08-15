"""PostgreSQL 持久化服务的公共导出。"""

from .models import CORE_SCHEMA, CORE_VERSION_TABLE, DatabaseBase
from .migration import (
    DatabaseMigrationStateError,
    DatabaseMigrator,
    PluginMigrationRegistry,
    PluginMigrationSpec,
)
from .plugins import (
    MAX_PLUGIN_ID_LENGTH,
    PLUGIN_SCHEMA_TOKEN,
    PluginRepositoryConstructor,
    PluginRepositoryBuilder,
    PluginSessionFactory,
    plugin_schema_name,
    validate_plugin_id,
)
from .plugin_migration import run_plugin_migration_environment
from .protocols import (
    GroupMessageReader,
    IncomingMessageWriter,
    RecallArchiver,
    SentMessageRecorder,
)
from .repository import PostgreSQLMessageRepository
from .runtime import PostgreSQLRuntime
from .schemas import (
    GroupDataScope,
    ImageArchiveStatus,
    MessageCursor,
    MessageDirection,
    StoredGroupImage,
    StoredGroupMessage,
)

__all__ = [
    "CORE_SCHEMA",
    "CORE_VERSION_TABLE",
    "MAX_PLUGIN_ID_LENGTH",
    "PLUGIN_SCHEMA_TOKEN",
    "DatabaseBase",
    "DatabaseMigrationStateError",
    "DatabaseMigrator",
    "GroupDataScope",
    "GroupMessageReader",
    "ImageArchiveStatus",
    "IncomingMessageWriter",
    "MessageCursor",
    "MessageDirection",
    "PluginRepositoryBuilder",
    "PluginRepositoryConstructor",
    "PluginMigrationRegistry",
    "PluginMigrationSpec",
    "PluginSessionFactory",
    "PostgreSQLMessageRepository",
    "PostgreSQLRuntime",
    "RecallArchiver",
    "SentMessageRecorder",
    "StoredGroupImage",
    "StoredGroupMessage",
    "plugin_schema_name",
    "run_plugin_migration_environment",
    "validate_plugin_id",
]
