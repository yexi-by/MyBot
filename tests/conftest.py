"""Pytest 进程级依赖初始化。"""

from collections.abc import Iterator
from pathlib import Path

import pytest

from app.utils.log import configure_logging


@pytest.fixture(scope="session", autouse=True)
def configure_test_logging(tmp_path_factory: pytest.TempPathFactory) -> Iterator[None]:
    """使用测试目录中的显式日志配置，不依赖生产兜底值。"""
    log_dir: Path = tmp_path_factory.mktemp("logs")
    configure_logging(
        log_dir=str(log_dir),
        console_level="CRITICAL",
        file_level="CRITICAL",
        retention="1 day",
        rotation="10 MB",
        compression="gz",
    )
    yield
