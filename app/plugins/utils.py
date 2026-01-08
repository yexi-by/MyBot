from app.models import GroupMessage, PrivateMessage
from app.utils import image_to_bytes_pathlib
from typing import Literal, overload


def aggregate_messages(
    msg: GroupMessage | PrivateMessage,
) -> tuple[list[str | int], list[str], list[str], int | None]:
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
                text_list.append(segment.data.text)
    return at_lst, text_list, image_url_lst, reply_id


def find_replied_message_image_paths(
    reply_message: GroupMessage | PrivateMessage,
) -> list[str]:
    image_path: list[str] = []
    for segment in reply_message.message:
        if segment.type != "image":
            continue
        assert segment.data.local_path is not None  # 同上 local path必定不是空
        image_path.append(segment.data.local_path)
    return image_path


def extract_text_from_message(text: str, token: str) -> str | None:
    if not text.startswith(token):
        return None
    prompt = text[len(token) :]
    return prompt


@overload
def get_response_images(
    image_path: list[str], output_type: Literal["base64"]
) -> list[str]: ...
@overload
def get_response_images(
    image_path: list[str], output_type: Literal["bytes"]
) -> list[bytes]: ...


def get_response_images(
    image_path: list[str], output_type: Literal["base64", "bytes"]
) -> list[str] | list[bytes]:
    image_lst = []
    for path in image_path:
        image = image_to_bytes_pathlib(image_path=path, output_type=output_type)
        image_lst.append(image)
    return image_lst
