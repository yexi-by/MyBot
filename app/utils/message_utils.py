from typing import TYPE_CHECKING, Any, Literal, overload

from app.models import GroupMessage, PrivateMessage, SelfMessage
from app.utils.log import logger
from app.utils.utils import read_file_content

if TYPE_CHECKING:
    from app.database.databasemanager import RedisDatabaseManager


def parse_message_chain(
    msg: GroupMessage | PrivateMessage,
) -> tuple[list[str | int], list[str], list[str], int | None]:
    """
    解析消息链，提取 At、图片 URL、文本和回复 ID。

    Args:
        msg: 群消息或私聊消息对象。

    Returns:
        tuple: 包含以下元素的元组:
            - at_lst (list[str | int]): 被 At 的用户 QQ 号列表。
            - text_list (list[str]): 文本内容列表。
            - image_url_lst (list[str]): 图片 URL 列表。
            - reply_id (int | None): 回复的消息 ID，如果没有则为 None。
    """
    at_lst: list[str | int] = []
    image_url_lst: list[str] = []
    text_list: list[str] = []
    reply_id: int | None = None

    for segment in msg.message:
        match segment.type:
            case "at":
                at_lst.append(segment.data.qq)
            case "image":
                assert (
                    segment.data.url is not None
                )  # 接受的消息 url 必定不是空的 但是发送的消息url是空的 两者共用一个模型 所以为了规避ide类型检查 这里断言
                image_url_lst.append(segment.data.url)
            case "reply":
                reply_id = segment.data.id
            case "text":
                text = segment.data.text.strip()
                text_list.append(text)
    return at_lst, text_list, image_url_lst, reply_id


def get_reply_image_paths(
    reply_message: GroupMessage | PrivateMessage | SelfMessage,
) -> list[str]:
    """
    从回复消息中提取本地图片路径。

    Args:
        reply_message: 回复的消息对象。

    Returns:
        list[str]: 本地图片路径列表。
    """
    image_path: list[str] = []
    for segment in reply_message.message:
        if segment.type != "image":
            continue
        assert segment.data.local_path is not None  # 同上 local path必定不是空
        image_path.append(segment.data.local_path)
    return image_path


def extract_text_from_message(text: str, token: str) -> str | None:
    """
    从消息文本中提取指令后的内容。

    Args:
        text: 消息文本。
        token: 指令前缀（如 "/" 或 "bot"）。

    Returns:
        str | None: 去除前缀后的内容，如果不匹配前缀则返回 None。
    """
    if not text.startswith(token):
        return None
    prompt = text[len(token) :]
    return prompt


@overload
def read_files_content(
    file_paths: list[str], output_type: Literal["base64"]
) -> list[str]: ...


@overload
def read_files_content(
    file_paths: list[str], output_type: Literal["bytes"]
) -> list[bytes]: ...


def read_files_content(
    file_paths: list[str], output_type: Literal["base64", "bytes"]
) -> list[str] | list[bytes]:
    """
    批量读取文件内容。

    Args:
        file_paths: 文件路径列表。
        output_type: 输出类型，'base64' 或 'bytes'。

    Returns:
        list[str] | list[bytes]: 文件内容列表。
    """
    content_lst = []
    for path in file_paths:
        content = read_file_content(file_path=path, output_type=output_type)
        content_lst.append(content)
    return content_lst


async def get_reply_message_from_db(
    database: "RedisDatabaseManager", self_id: int, group_id: int, reply_id: int
) -> GroupMessage | SelfMessage | None:
    """
    从数据库获取回复的消息，如果不存在则打印警告日志并返回 None。

    Args:
        database: 数据库管理器实例。
        self_id: 机器人自身的 QQ 号。
        group_id: 群号。
        reply_id: 回复的消息 ID。

    Returns:
        GroupMessage | SelfMessage | None: 回复的消息对象，如果未找到则返回 None。
    """
    reply_message = await database.search_messages(
        self_id=self_id, group_id=group_id, message_id=reply_id
    )
    if not reply_message:
        logger.warning(
            f"redis没有查到数据,请检查群号 {group_id} ,被回复的消息id: {reply_id} 是否在数据库中"
        )
        return None
    return reply_message
