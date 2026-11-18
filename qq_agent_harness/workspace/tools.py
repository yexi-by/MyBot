"""共享工作区工具目录：文件读写改搜删与本地受控命令执行。"""

import asyncio

from pydantic import Field, field_validator, model_validator

from ..contracts import (
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
)
from .executor import ExecuteRequest, WorkspaceBusyError, WorkspaceOverloadedError, WorkspaceService
from .storage import WorkspaceUnavailable

CATEGORY_LABELS = {"workspace": "共享工作区"}


def _check_relative(value: str) -> str:
    if "\\" in value or ":" in value or "\x00" in value or value.startswith("/"):
        raise ValueError("必须使用工作区内的相对 POSIX 路径")
    if ".." in value.split("/"):
        raise ValueError("路径不能包含 ..")
    return value


class ExecInput(Model):
    command: str = Field(
        min_length=1,
        max_length=65_536,
        description=(
            "在工作区执行的命令。单次命令临时中间文件请写入 $TMPDIR（执行完自动清理），"
            "跨步骤临时素材放入 .scratch/，长期复用的脚本与成果按主题放入子目录。"
        ),
    )
    cwd: str = Field(default="", description="工作区内的相对子目录；留空表示工作区根目录")
    timeout: float | None = Field(
        default=None,
        gt=0,
        le=86_400,
        description="可选：本次命令超时秒数（不超过工作区配置的 command_timeout 上限）",
    )

    @field_validator("cwd")
    @classmethod
    def valid_cwd(cls, value: str) -> str:
        return _check_relative(value) if value else value


class FileInput(Model):
    path: str = Field(min_length=1, max_length=512, description="工作区内的相对文件路径")

    @field_validator("path")
    @classmethod
    def valid_path(cls, value: str) -> str:
        return _check_relative(value)


class ReadInput(FileInput):
    offset: int = Field(default=0, ge=0, description="读取起始字节偏移（UTF-8 安全分页）")
    limit: int = Field(default=65_536, ge=1, le=1_048_576, description="单次最多读取的字节数")


class WriteInput(FileInput):
    content: str = Field(description="要写入的 UTF-8 文本内容")
    overwrite: bool = Field(default=False, description="目标文件已存在时是否允许覆盖")
    append: bool = Field(default=False, description="是否追加到文件末尾（文件不存在时自动新建）")

    @model_validator(mode="after")
    def modifying_existing(self) -> WriteInput:
        # 追加同样会修改已有文件，不能绕过群策略对 overwrite 的限制。
        if self.append:
            self.overwrite = True
        return self


class EditInput(FileInput):
    old_string: str = Field(min_length=1, description="要替换的原始精确文本片段")
    new_string: str = Field(description="替换后的新文本片段")
    replace_all: bool = Field(
        default=False,
        description="存在多处相同匹配时是否全部替换（默认 false，要求唯一匹配）",
    )


class ListInput(Model):
    path: str = Field(default="", description="工作区内的相对目录路径；留空表示根目录")
    offset: int = Field(default=0, ge=0, description="分页起始索引")
    limit: int = Field(default=100, ge=1, le=500, description="单页返回的最大条目数")

    @field_validator("path")
    @classmethod
    def valid_dir(cls, value: str) -> str:
        return _check_relative(value) if value else value


class SearchInput(Model):
    query: str = Field(
        default="",
        description="要搜索的文本内容；留空则仅按 glob 模式列出匹配文件",
    )
    path: str = Field(default="", description="限定搜索的工作区相对子目录，留空表示整个工作区")
    glob: str = Field(
        default="*",
        description="文件名或相对路径匹配模式（如 *.py、**/*.md、exports/*.png）",
    )
    regex: bool = Field(default=False, description="query 是否按正则表达式解析")
    case_sensitive: bool = Field(default=False, description="是否区分大小写")
    include_hidden: bool = Field(
        default=False, description="是否包含 .scratch 等隐藏目录与缓存目录（默认跳过）"
    )
    max_results: int = Field(default=100, ge=1, le=500, description="最多返回的匹配项数")

    @field_validator("path")
    @classmethod
    def valid_search_dir(cls, value: str) -> str:
        return _check_relative(value) if value else value


class DeleteInput(FileInput):
    recursive: bool = Field(
        default=False, description="目标为非空子目录时，是否连同内部所有文件递归删除"
    )


type WorkspaceRequest = (
    ExecInput | ReadInput | WriteInput | EditInput | ListInput | SearchInput | DeleteInput
)


def build_tools(service: WorkspaceService) -> list[ToolDefinition]:
    async def dispatch(context: ToolContext, request: WorkspaceRequest) -> ToolResult:
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        if denied := context.recheck():
            return denied
        try:
            if isinstance(request, ExecInput):
                task = asyncio.create_task(
                    service.execute(
                        ExecuteRequest(
                            command=request.command,
                            cwd=request.cwd,
                            timeout=request.timeout,
                            owner=context.turn_id,
                        )
                    )
                )
                watcher = asyncio.create_task(_watch_cancel(context, service, task))
                try:
                    result = await task
                finally:
                    watcher.cancel()
                    await asyncio.gather(watcher, return_exceptions=True)
                exec_status = str(result.get("status", "ok"))
                if exec_status == "cancelled":
                    return ToolResult(status="cancelled", message="命令已停止", data=result)
                if exec_status == "timeout":
                    return ToolResult(
                        status="error",
                        error_type="CommandTimeout",
                        message="命令执行超时，进程已终止",
                        data=result,
                    )
                if exec_status == "error":
                    return ToolResult(
                        status="error",
                        error_type="NonZeroExit",
                        message=f"命令返回非零退出码：{result.get('exit_code')}",
                        data=result,
                    )
                return ToolResult.success(result)

            if isinstance(request, ReadInput):
                return ToolResult.success(
                    await service.read_text(request.path, request.offset, request.limit)
                )
            if isinstance(request, WriteInput):
                return ToolResult.success(
                    await service.write_file(
                        request.path,
                        request.content,
                        overwrite=request.overwrite,
                        append=request.append,
                    )
                )
            if isinstance(request, EditInput):
                return ToolResult.success(
                    await service.edit_file(
                        request.path,
                        request.old_string,
                        request.new_string,
                        replace_all=request.replace_all,
                    )
                )
            if isinstance(request, ListInput):
                return ToolResult.success(
                    await service.files(request.path, request.offset, request.limit)
                )
            if isinstance(request, SearchInput):
                return ToolResult.success(
                    await service.search(
                        request.query,
                        path=request.path,
                        glob=request.glob,
                        regex=request.regex,
                        case_sensitive=request.case_sensitive,
                        include_hidden=request.include_hidden,
                        max_results=request.max_results,
                    )
                )
            if isinstance(request, DeleteInput):
                return ToolResult.success(
                    await service.delete_path(request.path, recursive=request.recursive)
                )
        except FileNotFoundError:
            return ToolResult.failure("指定的文件或目录不存在", "FileNotFound")
        except FileExistsError:
            return ToolResult.failure(
                "目标文件已存在；如需覆盖请设置 overwrite=true，局部修改请用 workspace__edit_file",
                "FileExists",
            )
        except WorkspaceBusyError as exc:
            return ToolResult.failure(str(exc), "WorkspaceBusy")
        except WorkspaceOverloadedError as exc:
            return ToolResult.failure(str(exc), "WorkspaceOverloaded")
        except WorkspaceUnavailable as exc:
            return ToolResult.failure(str(exc), "WorkspaceUnavailable")
        except ValueError as exc:
            return ToolResult.failure(str(exc), "InvalidOperation")
        except OSError as exc:
            return ToolResult.failure(
                f"工作区文件操作失败（{type(exc).__name__}）", "WorkspaceError"
            )
        return ToolResult.failure("未知工作区请求", "InvalidOperation")

    return [
        ToolDefinition(
            "workspace__exec",
            (
                "在持久化共享工作区直接执行本地命令（内置 Python 3.14、uv、Node.js、git、rg、jq、ffmpeg、"
                "中文字体及常用绘图与文档库；额外依赖可用 uv venv .venv 安装并跨轮保留）。"
                "单次命令临时文件请写到 $TMPDIR，跨步骤临时素材放 .scratch/。"
            ),
            "workspace",
            model_binding(ExecInput, dispatch),
            title="执行工作区命令",
        ),
        ToolDefinition(
            "workspace__read_file",
            "按 UTF-8 安全字节偏移分段读取共享工作区中的文本文件。",
            "workspace",
            model_binding(ReadInput, dispatch),
            concurrency_safe=True,
            title="读取工作区文本",
        ),
        ToolDefinition(
            "workspace__write_file",
            "在共享工作区新建、覆盖或追加 UTF-8 文本文件；局部修改已有文件请优先使用 workspace__edit_file。",
            "workspace",
            model_binding(WriteInput, dispatch),
            title="写入工作区文本",
        ),
        ToolDefinition(
            "workspace__edit_file",
            "精确替换共享工作区文本文件中的指定片段，避免整份重写大文件。",
            "workspace",
            model_binding(EditInput, dispatch),
            title="编辑工作区文本",
        ),
        ToolDefinition(
            "workspace__list_files",
            "分页列出共享工作区指定目录下的文件与子目录（含大小与修改时间）。",
            "workspace",
            model_binding(ListInput, dispatch),
            concurrency_safe=True,
            title="列出工作区文件",
        ),
        ToolDefinition(
            "workspace__search_files",
            "在共享工作区中按文件名模式（glob）查找文件，或按关键词/正则表达式检索文本行（默认跳过缓存与虚拟环境目录）。",
            "workspace",
            model_binding(SearchInput, dispatch),
            concurrency_safe=True,
            title="搜索工作区文件",
        ),
        ToolDefinition(
            "workspace__delete_path",
            "删除共享工作区中的无用文件或临时子目录（不能越界或删除系统保留根目录）。",
            "workspace",
            model_binding(DeleteInput, dispatch),
            title="删除工作区路径",
        ),
    ]


async def _watch_cancel(
    context: ToolContext, service: WorkspaceService, task: asyncio.Task[object]
) -> None:
    while not task.done():
        if context.cancelled():
            await service.cancel(context.turn_id)
            return
        await asyncio.sleep(0.05)
