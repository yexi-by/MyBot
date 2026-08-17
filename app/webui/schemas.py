"""WebUI 配置 API 的请求与响应模型。"""

from typing import Any, Literal

from pydantic import BaseModel


class ConfigIssuePayload(BaseModel):
    """一条脱敏的配置错误，直接对应 config.ConfigIssue。"""

    location: str
    error_type: str
    message: str


class ConfigMeta(BaseModel):
    """当前配置运行态元信息。"""

    plugin_revision: int
    watcher_active: bool
    restart_only_sections: list[str]
    restart_required_sections: list[str]
    boot_id: str


class ConfigGetResponse(BaseModel):
    """当前配置文件的原始内容、哈希和校验状态。"""

    config: dict[str, Any]
    sha256: str
    valid: bool
    issues: list[ConfigIssuePayload]
    meta: ConfigMeta


class ConfigValidateRequest(BaseModel):
    """完整配置 JSON 的 dry-run 校验请求。"""

    config: dict[str, Any]


class ConfigValidateResponse(BaseModel):
    """dry-run 校验结果。"""

    valid: bool
    issues: list[ConfigIssuePayload]


class ConfigSaveRequest(BaseModel):
    """完整配置 JSON 的保存请求，带读取时的内容哈希做乐观锁。"""

    config: dict[str, Any]
    base_sha256: str


class ConfigSaveResponse(BaseModel):
    """配置保存结果与相对启动配置的待重启节。"""

    config: dict[str, Any]
    sha256: str
    restart_required_sections: list[str]


class FileListResponse(BaseModel):
    """config/ 目录内可编辑文本文件的相对路径列表。"""

    files: list[str]


class FileGetResponse(BaseModel):
    """单个文本文件的内容与哈希。"""

    path: str
    content: str
    sha256: str


class FileSaveRequest(BaseModel):
    """文本文件保存请求；base_sha256 为空表示不检查冲突（仅新建时）。"""

    content: str
    base_sha256: str | None = None


class FileSaveResponse(BaseModel):
    """文本文件保存结果。"""

    sha256: str


class PowerResponse(BaseModel):
    """电源操作受理结果；进程会在响应返回后优雅停机。"""

    ok: bool
    action: Literal["restart", "shutdown"]
    message: str
