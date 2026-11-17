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
    timeout: float | None = Field(
        default=None, gt=0, description="命令最大运行时长（秒）；省略时使用沙箱默认超时"
    )


class FileInput(Model):
    path: str = Field(min_length=1, description="工作区内的相对文件路径")


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


class EditInput(FileInput):
    old_string: str = Field(min_length=1, description="要替换的原文本片段（须包含精确缩进与换行）")
    new_string: str = Field(description="替换后的新文本片段（传空字符串可删除匹配片段）")
    replace_all: bool = Field(
        default=False,
        description="为 false 时要求 old_string 在文件中唯一出现；为 true 时替换全部匹配",
    )


class ReadInput(FileInput):
    offset: int = Field(
        default=0, ge=0, description="读取起始字节偏移，续读时传入上次返回的 next_offset"
    )
    limit: int = Field(default=65_536, ge=1, description="单次最大读取字节数")


class ListInput(Model):
    path: str = Field(default="", description="工作区内的相对目录，留空表示根目录")
    offset: int = Field(default=0, ge=0, description="分页起始位置")
    limit: int = Field(default=100, ge=1, description="单页返回数量")


class SearchInput(Model):
    query: str = Field(
        default="",
        description="要搜索的文本内容；留空则仅按 glob 模式列出匹配文件",
    )
    path: str = Field(default="", description="限定搜索的工作区相对子目录，留空表示整个工作区")
    glob: str = Field(
        default="*",
        description="文件名或相对路径匹配模式（如 *.py、**/*.md、assets/*.png）",
    )
    regex: bool = Field(default=False, description="query 是否按正则表达式解析")
    case_sensitive: bool = Field(default=False, description="是否区分大小写")
    include_hidden: bool = Field(
        default=False, description="是否包含 .scratch 等隐藏目录与缓存目录（默认跳过）"
    )
    max_results: int = Field(default=100, ge=1, le=500, description="最多返回的匹配项数")


class DeleteInput(FileInput):
    recursive: bool = Field(
        default=False, description="目标为非空子目录时，是否连同内部所有文件递归删除"
    )


type SandboxRequest = (
    ExecInput | ReadInput | WriteInput | EditInput | ListInput | SearchInput | DeleteInput
)


def build_tools(
    get_settings: Callable[[], SandboxSettings], client: RunnerClient | None = None
) -> list[ToolDefinition]:
    async def dispatch(context: ToolContext, request: SandboxRequest) -> ToolResult:
        operation = {
            ExecInput: "exec",
            ReadInput: "read",
            WriteInput: "write",
            EditInput: "edit",
            ListInput: "list",
            SearchInput: "search",
            DeleteInput: "delete",
        }[type(request)]
        owned = client is None
        connection = client or RunnerClient(get_settings)
        if context.cancelled():
            return ToolResult(status="cancelled", message="当前轮次已停止")
        try:
            if isinstance(request, ExecInput):
                if denied := context.recheck():
                    return denied
                result = await connection.execute(
                    request.command, request.cwd, context.turn_id, timeout=request.timeout
                )
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
            if isinstance(request, EditInput):
                if denied := context.recheck():
                    return denied
                return ToolResult.success(
                    await connection.edit_file(
                        request.path,
                        request.old_string,
                        request.new_string,
                        replace_all=request.replace_all,
                    )
                )
            if isinstance(request, SearchInput):
                return ToolResult.success(
                    await connection.search_files(
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
                if denied := context.recheck():
                    return denied
                return ToolResult.success(
                    await connection.delete_path(request.path, recursive=request.recursive)
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
            if code == "InvalidOperation" and isinstance(detail := payload.get("detail"), str):
                return ToolResult.failure(detail, "InvalidOperation")
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
            (
                "在隔离沙箱中运行 bash 命令，适合计算、数据处理、脚本执行与文件转换。"
                "预装 Python 3.14（uv、requests/httpx/bs4/lxml/pyyaml、numpy/pandas/scipy/sympy、"
                "matplotlib/seaborn/pillow/qrcode/imageio/cairosvg/resvg_py、pymupdf/pypdf/pdfplumber/"
                "openpyxl/python-docx/python-pptx/reportlab）、Node.js 24（npm/npx）、git/rg/fd/jq/sqlite3、"
                "zip/7z/tar、ffmpeg/imagemagick/poppler/qpdf/pandoc/graphviz/tesseract（中英）及中文字体。"
                "目录规范：单次命令内用完即弃的中间文件（如抽帧切片、临时编译产物）直接写 /tmp（命令结束自动销毁）；"
                "跨步骤临时文件放 .scratch/；仅需长期复用的脚本与最终成果按主题建子目录存放，勿将散碎临时文件堆在工作区根目录。"
            ),
            "sandbox",
            model_binding(ExecInput, dispatch),
            title="执行代码",
        ),
        ToolDefinition(
            "sandbox__read_file",
            "读取沙箱工作区中的 UTF-8 文本文件（支持按字节偏移分段读取）。",
            "sandbox",
            model_binding(ReadInput, dispatch),
            concurrency_safe=True,
            title="读取工作区文件",
        ),
        ToolDefinition(
            "sandbox__write_file",
            "向沙箱工作区新建、覆盖或追加 UTF-8 文本文件；局部修改已有文件请优先用 sandbox__edit_file。",
            "sandbox",
            model_binding(WriteInput, dispatch),
            title="写入工作区文件",
        ),
        ToolDefinition(
            "sandbox__edit_file",
            "精确替换沙箱工作区 UTF-8 文本文件中的指定片段，无需整份重写或启动容器。",
            "sandbox",
            model_binding(EditInput, dispatch),
            title="编辑工作区文件",
        ),
        ToolDefinition(
            "sandbox__list_files",
            "列出沙箱工作区指定目录中的文件与子目录。",
            "sandbox",
            model_binding(ListInput, dispatch),
            concurrency_safe=True,
            title="列出工作区文件",
        ),
        ToolDefinition(
            "sandbox__search_files",
            "在沙箱工作区递归查找匹配文件（glob）或检索文本内容（grep），默认跳过缓存与依赖环境目录。",
            "sandbox",
            model_binding(SearchInput, dispatch),
            concurrency_safe=True,
            title="搜索工作区文件",
        ),
        ToolDefinition(
            "sandbox__delete_path",
            "删除沙箱工作区内的指定文件或子目录（用于清理不再需要的中间文件或旧目录）。",
            "sandbox",
            model_binding(DeleteInput, dispatch),
            title="删除工作区文件",
        ),
    ]
