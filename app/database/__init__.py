"""PostgreSQL 持久化服务的公共导出。"""

from .models import CORE_SCHEMA, CORE_VERSION_TABLE, DatabaseBase
from .migration import (
    DatabaseMigrationStateError,
    DatabaseMigrator,
)
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
    MediaArchiveStatus,
    MessageCursor,
    MessageDirection,
    StoredGroupMedia,
    StoredGroupMessage,
)

__all__ = [
    "CORE_SCHEMA",
    "CORE_VERSION_TABLE",
    "DatabaseBase",
    "DatabaseMigrationStateError",
    "DatabaseMigrator",
    "GroupDataScope",
    "GroupMessageReader",
    "MediaArchiveStatus",
    "IncomingMessageWriter",
    "MessageCursor",
    "MessageDirection",
    "PostgreSQLMessageRepository",
    "PostgreSQLRuntime",
    "RecallArchiver",
    "SentMessageRecorder",
    "StoredGroupMedia",
    "StoredGroupMessage",
]
