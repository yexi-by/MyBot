"""WebUI 配置 API 路由；ConfigManager 在创建路由时显式注入。"""

from uuid import uuid4

from fastapi import APIRouter, HTTPException, Response, status

from app.config import RESTART_ONLY_SECTIONS, ConfigLoadError, ConfigManager

from . import config_io, files
from .config_io import ConfigConflictError
from .power import PowerAction, PowerController
from .schemas import (
    ConfigGetResponse,
    ConfigIssuePayload,
    ConfigMeta,
    ConfigSaveRequest,
    ConfigSaveResponse,
    ConfigValidateRequest,
    ConfigValidateResponse,
    FileGetResponse,
    FileListResponse,
    FileSaveRequest,
    FileSaveResponse,
    PowerResponse,
)


def _issue_payloads(error: ConfigLoadError) -> list[ConfigIssuePayload]:
    """把脱敏配置错误转换为 API 载荷。"""
    return [
        ConfigIssuePayload(
            location=issue.location,
            error_type=issue.error_type,
            message=issue.message,
        )
        for issue in error.issues
    ]


def _is_invalid_path_error(error: ConfigLoadError) -> bool:
    """区分非法文本路径与普通的文件不存在。"""
    return any(
        issue.error_type
        in {
            "path_escape",
            "absolute_path",
            "invalid_path",
            "unsupported_file_type",
        }
        for issue in error.issues
    )


def create_webui_router(
    *,
    manager: ConfigManager,
    watcher_active: bool,
    power: PowerController | None = None,
) -> APIRouter:
    """创建配置与文本文件 API 路由，并绑定配置管理器。"""
    router = APIRouter(prefix="/api")
    boot_id = uuid4().hex

    @router.get("/config")
    def get_config(response: Response) -> ConfigGetResponse:
        """返回当前配置文件内容、哈希、校验状态与运行态元信息。"""
        response.headers["Cache-Control"] = "no-store"
        result = config_io.read_config_payload(config_file=manager.config_file)
        restart_now = (
            list(
                config_io.restart_sections(
                    boot_config=manager.boot_config, candidate=result.parsed
                )
            )
            if result.parsed is not None
            else []
        )
        return ConfigGetResponse(
            config=result.config,
            sha256=result.sha256,
            valid=result.valid,
            issues=[
                ConfigIssuePayload(
                    location=issue.location,
                    error_type=issue.error_type,
                    message=issue.message,
                )
                for issue in result.issues
            ],
            meta=ConfigMeta(
                plugin_revision=manager.plugins.revision,
                watcher_active=watcher_active,
                restart_only_sections=list(RESTART_ONLY_SECTIONS),
                restart_required_sections=restart_now,
                boot_id=boot_id,
            ),
        )

    @router.post("/config/validate")
    def validate_config(body: ConfigValidateRequest) -> ConfigValidateResponse:
        """dry-run 校验完整配置，不落盘。"""
        try:
            _ = config_io.parse_and_validate(
                config_file=manager.config_file, payload=body.config
            )
        except ConfigLoadError as exc:
            return ConfigValidateResponse(valid=False, issues=_issue_payloads(exc))
        return ConfigValidateResponse(valid=True, issues=[])

    @router.put("/config")
    def save_config(body: ConfigSaveRequest) -> ConfigSaveResponse:
        """校验并写回完整配置；冲突返回 409，校验失败返回 422。"""
        try:
            result = config_io.write_config_payload(
                config_file=manager.config_file,
                payload=body.config,
                base_sha256=body.base_sha256,
                boot_config=manager.boot_config,
            )
        except ConfigConflictError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        except ConfigLoadError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"issues": [issue.model_dump() for issue in _issue_payloads(exc)]},
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="配置文件不可写，请检查 config 目录挂载权限",
            ) from exc
        return ConfigSaveResponse(
            config=result.config,
            sha256=result.sha256,
            restart_required_sections=list(result.restart_required_sections),
        )

    @router.get("/files")
    def list_files(response: Response) -> FileListResponse:
        """列出 config/ 内可编辑的文本文件。"""
        response.headers["Cache-Control"] = "no-store"
        return FileListResponse(
            files=files.list_text_files(config_root=manager.config_root)
        )

    @router.get("/files/{relative_path:path}")
    def read_file(relative_path: str, response: Response) -> FileGetResponse:
        """读取单个文本文件；逃逸 422，不存在 404。"""
        response.headers["Cache-Control"] = "no-store"
        try:
            content, digest = files.read_text_file(
                config_root=manager.config_root, relative_path=relative_path
            )
        except ConfigLoadError as exc:
            raise HTTPException(
                (
                    status.HTTP_422_UNPROCESSABLE_CONTENT
                    if _is_invalid_path_error(exc)
                    else status.HTTP_404_NOT_FOUND
                ),
                detail={"issues": [issue.model_dump() for issue in _issue_payloads(exc)]},
            ) from exc
        return FileGetResponse(path=relative_path, content=content, sha256=digest)

    @router.put("/files/{relative_path:path}")
    def save_file(relative_path: str, body: FileSaveRequest) -> FileSaveResponse:
        """写回单个文本文件；逃逸 422，冲突 409。"""
        try:
            digest = files.write_text_file(
                config_root=manager.config_root,
                relative_path=relative_path,
                content=body.content,
                base_sha256=body.base_sha256,
            )
        except ConfigConflictError as exc:
            raise HTTPException(
                status.HTTP_409_CONFLICT, detail=str(exc)
            ) from exc
        except ConfigLoadError as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail={"issues": [issue.model_dump() for issue in _issue_payloads(exc)]},
            ) from exc
        except OSError as exc:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="文本文件不可写，请检查 config 目录挂载权限",
            ) from exc
        return FileSaveResponse(sha256=digest)

    def _request_power(action: PowerAction) -> PowerResponse:
        """受理电源操作：延迟优雅停机，是否重新拉起由外部守护策略决定。"""
        if power is None or not power.available:
            raise HTTPException(
                status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="当前运行模式未启用电源控制",
            )
        power.request(action)
        return PowerResponse(
            ok=True,
            action=action,
            message=(
                "已受理重启请求，进程将优雅退出；是否自动拉起取决于部署的守护策略"
                if action == "restart"
                else "已受理关机请求，进程将优雅退出"
            ),
        )

    @router.post("/system/restart", status_code=status.HTTP_202_ACCEPTED)
    async def restart_process() -> PowerResponse:
        """重启进程：优雅停机后由 Docker restart 策略或运维手动拉起。"""
        return _request_power("restart")

    @router.post("/system/shutdown", status_code=status.HTTP_202_ACCEPTED)
    async def shutdown_process() -> PowerResponse:
        """关机：优雅停机；Docker 守护下会被重新拉起，效果等同重启。"""
        return _request_power("shutdown")

    _ = (
        get_config,
        validate_config,
        save_config,
        list_files,
        read_file,
        save_file,
        restart_process,
        shutdown_process,
    )
    return router
