"""NapCat 群文件信息工具。"""

from app.models import GroupMessage, JsonObject, JsonValue, to_json_value
from app.services.llm.tools import LLMToolRegistry

from .arguments import (
    GetGroupFileUrlArgs,
    ListGroupFilesByFolderArgs,
    ListGroupRootFilesArgs,
)
from .protocols import NapCatGroupToolBot


class GroupFileToolset:
    """把当前群文件查询能力暴露为 LLM 信息工具。"""

    def __init__(self, *, bot: NapCatGroupToolBot, event: GroupMessage) -> None:
        """绑定当前群事件。"""
        self.bot: NapCatGroupToolBot = bot
        self.event: GroupMessage = event

    def register_tools(self, registry: LLMToolRegistry) -> None:
        """向工具注册表登记群文件工具。"""
        registry.register_tool(
            name="qq__list_group_root_files",
            description=(
                "信息工具：查看当前群群文件根目录列表。"
                "当用户询问群文件、想找文件、需要先确认 file_id 时使用。"
                "此工具只返回文件列表，不发送群消息。"
            ),
            parameters_model=ListGroupRootFilesArgs,
            handler=self.list_group_root_files,
        )
        registry.register_tool(
            name="qq__list_group_files_by_folder",
            description=(
                "信息工具：查看当前群某个群文件夹内的文件列表。"
                "需要先知道 folder_id 或文件夹路径/名称；如果不知道文件夹标识，先调用 qq__list_group_root_files。"
                "此工具只返回文件列表，不发送群消息。"
            ),
            parameters_model=ListGroupFilesByFolderArgs,
            handler=self.list_group_files_by_folder,
        )
        registry.register_tool(
            name="qq__get_group_file_url",
            description=(
                "信息工具：获取当前群指定群文件的下载链接和 NapCat 原始响应。"
                "需要先通过群文件列表确认 file_id。此工具只返回链接信息，不发送群消息。"
            ),
            parameters_model=GetGroupFileUrlArgs,
            handler=self.get_group_file_url,
        )

    async def list_group_root_files(self, arguments: JsonObject) -> JsonValue:
        """查询当前群文件根目录。"""
        args = ListGroupRootFilesArgs.model_validate(arguments)
        response = await self.bot.get_group_root_files(
            group_id=self.event.group_id,
            file_count=args.file_count,
        )
        return {
            "ok": True,
            "action": "list_group_root_files",
            "group_id": to_json_value(self.event.group_id),
            "response": to_json_value(response),
        }

    async def list_group_files_by_folder(self, arguments: JsonObject) -> JsonValue:
        """查询当前群指定文件夹的文件列表。"""
        args = ListGroupFilesByFolderArgs.model_validate(arguments)
        response = await self.bot.get_group_files_by_folder(
            group_id=self.event.group_id,
            folder_id=args.folder_id,
            folder=args.folder,
            file_count=args.file_count,
        )
        return {
            "ok": True,
            "action": "list_group_files_by_folder",
            "group_id": to_json_value(self.event.group_id),
            "folder_id": args.folder_id,
            "folder": args.folder,
            "response": to_json_value(response),
        }

    async def get_group_file_url(self, arguments: JsonObject) -> JsonValue:
        """获取当前群指定文件的下载链接。"""
        args = GetGroupFileUrlArgs.model_validate(arguments)
        response = await self.bot.get_group_file_url(
            group_id=self.event.group_id,
            file_id=args.file_id,
        )
        return {
            "ok": True,
            "action": "get_group_file_url",
            "group_id": to_json_value(self.event.group_id),
            "file_id": args.file_id,
            "response": to_json_value(response),
        }
