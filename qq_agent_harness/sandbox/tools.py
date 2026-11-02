"""代码与共享文件工具；群策略由统一工具执行层检查。"""

import asyncio
from collections.abc import Callable

import httpx
from pydantic import Field, model_validator

from ..contracts import (
    JSON_OBJECT,
    JsonObject,
    Model,
    ToolContext,
    ToolDefinition,
    ToolResult,
    model_binding,
)
from ..settings import SandboxSettings
from .client import RunnerClient

CATEGORY_LABELS = {"sandbox": "沙箱"}


class ExecInput(Model):
    command: str = Field(
        min_length=1,
        description="要在沙箱中执行的 bash 命令",
    )
    cwd: str = Field(default="", description="工作区内的相对目录，默认为工作区根目录")


class FileInput(Model):
    path: str = Field(description="工作区内的相对文件路径")


class WriteInput(FileInput):
    content: str = Field(description="要写入的 UTF-8 文本；大文件可分次追加")
    overwrite: bool = Field(default=False, description="是否覆盖同名已有文件")
    append: bool = Field(default=False, description="是否追加到文件末尾（文件不存在时自动新建）")

    @model_validator(mode="after")
    def modifying_existing(self) -> WriteInput:
        # 追加同样会修改已有文件，不能绕过群策略对 overwrite 的限制。
        if self.append:
            self.overwrite = True
        return self


class ReadInput(FileInput):
    offset: int = Field(
        default=0, ge=0, description="读取起始字节偏移，续读时传入上次返回的 next_offset"
    )
    limit: int = Field(default=65_536, ge=1, description="单次最大读取字节数")


class ListInput(Model):
    path: str = Field(default="", description="工作区内的相对目录，留空表示根目录")
    offset: int = Field(default=0, ge=0, description="分页起始位置")
    limit: int = Field(default=100, ge=1, description="单页返回数量")


def build_tools(
    get_settings: Callable[[], SandboxSettings], client: RunnerClient | None = None
) -> list[ToolDefinition]:
    async def dispatch(
        context: ToolContext, request: ExecInput | ReadInput | WriteInput | ListInput
    ) -> ToolResult:
        operation = {ExecInput: "exec", ReadInput: "read", WriteInput: "write", ListInput: "list"}[
            type(request)
        ]
        owned = client is None
        connection = client or RunnerClient(get_settings)
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        try:
            if isinstance(request, ExecInput):
                if denied := context.recheck():
                    return denied
                result = await connection.execute(request.command, request.cwd, context.turn_id)
                state = result.get("status")
                if state == "cancelled":
                    return ToolResult(status="cancelled", data=result, message="代码执行已停止")
                if state == "timeout":
                    return ToolResult.failure(
                        "代码执行超时并已停止", "ExecutionTimeout"
                    ).model_copy(update={"data": result})
                if state == "error":
                    return ToolResult.failure(
                        "命令退出码非零，请检查输出", "ExecutionError"
                    ).model_copy(update={"data": result})
                return ToolResult.success(result)
            if isinstance(request, ReadInput):
                return ToolResult.success(
                    await connection.read_text(request.path, request.offset, request.limit)
                )
            if isinstance(request, WriteInput):
                if denied := context.recheck():
                    return denied
                return ToolResult.success(
                    await connection.write_file(
                        request.path, request.content, request.overwrite, request.append
                    )
                )
            return ToolResult.success(
                await connection.files(request.path, request.offset, request.limit)
            )
        except asyncio.CancelledError:
            raise
        except httpx.TimeoutException:
            return ToolResult.failure("沙箱请求超时", "ExecutionTimeout")
        except httpx.HTTPStatusError as error:
            if error.response.status_code == 429:
                return ToolResult.failure("沙箱队列已满，请稍后重试", "WorkspaceQueueFull")
            if error.response.status_code == 409:
                return ToolResult.failure("沙箱正在忙，请稍后重试", "WorkspaceBusy")
            payload: JsonObject = {}
            try:
                payload = JSON_OBJECT.validate_json(error.response.content)
            except ValueError:
                pass
            code = payload.get("error_type")
            known = {
                "FileNotFound": "文件或目录不存在",
                "FileExists": "目标已存在，如需修改请开启 overwrite 或 append",
                "StorageFull": "磁盘空间不足",
                "QuotaExceeded": "工作区磁盘配额已满，请先清理文件",
                "AccessDenied": "文件访问权限不足",
                "FilesystemError": "文件操作失败",
            }
            if isinstance(code, str) and code in known:
                return ToolResult.failure(known[code], code)
            return ToolResult.failure(
                f"沙箱请求失败（HTTP {error.response.status_code}，操作 {operation}）",
                "SandboxError",
            )
        except httpx.HTTPError, ValueError, UnicodeError:
            return ToolResult.failure("沙箱不可用或文件无法作为 UTF-8 文本读取", "SandboxError")
        finally:
            if owned:
                await connection.close()

    return [
        ToolDefinition(
            "sandbox__exec",
            "在隔离沙箱中运行 bash 命令，适合计算、数据处理、脚本执行与文件转换。预装 Python 3.14（uv、requests/httpx/bs4/lxml/pyyaml、numpy/pandas/scipy/sympy、matplotlib/seaborn/pillow/qrcode/imageio、pymupdf/pypdf/pdfplumber/openpyxl/python-docx/python-pptx/reportlab）、Node.js 24（npm/npx）、git/rg/fd/jq/sqlite3、zip/7z/tar、ffmpeg/imagemagick/poppler/qpdf/pandoc/graphviz/tesseract（中英）及中文字体；根目录只读，额外依赖可用 uv 装入工作区。",
            "sandbox",
            model_binding(ExecInput, dispatch),
            title="执行代码",
        ),
        ToolDefinition(
            "sandbox__read_file",
            "读取沙箱工作区中的 UTF-8 文本文件。",
            "sandbox",
            model_binding(ReadInput, dispatch),
            title="读取工作区文件",
        ),
        ToolDefinition(
            "sandbox__write_file",
            "向沙箱工作区写入或追加 UTF-8 文本文件。",
            "sandbox",
            model_binding(WriteInput, dispatch),
            title="写入工作区文件",
        ),
        ToolDefinition(
            "sandbox__list_files",
            "列出沙箱工作区目录中的文件与子目录。",
            "sandbox",
            model_binding(ListInput, dispatch),
            title="列出工作区文件",
        ),
    ]
