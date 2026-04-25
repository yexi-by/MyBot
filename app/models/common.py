"""NapCat 协议通用类型。"""

from typing import Annotated, ClassVar, cast

from pydantic import BaseModel, BeforeValidator, ConfigDict

type JsonScalar = str | int | float | bool | None
type JsonValue = JsonScalar | list[JsonValue] | dict[str, JsonValue]
type JsonObject = dict[str, JsonValue]


class StrictModel(BaseModel):
    """项目 Pydantic 模型基类，默认拒绝未知字段。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(extra="forbid")


class NapCatModel(BaseModel):
    """NapCat 入站协议模型基类，吸收上游字段漂移。"""

    model_config: ClassVar[ConfigDict] = ConfigDict(
        extra="ignore",
        coerce_numbers_to_str=True,
    )


def normalize_string_or_integer(value: object) -> str:
    """将 NapCat 文档中的 string/integer 双类型字段统一收敛为字符串。"""
    # Pydantic 校验器入口只能接收 object；这里立即收窄到协议允许的标量类型。
    if isinstance(value, bool):
        raise ValueError("NapCat 字符串/整数字段不能是布尔值")
    if isinstance(value, int):
        return str(value)
    if isinstance(value, str):
        cleaned_value = value.strip()
        if cleaned_value == "":
            raise ValueError("NapCat 字符串/整数字段不能为空")
        return cleaned_value
    raise ValueError("NapCat 字符串/整数字段必须是字符串或整数")


type NapCatStringInteger = Annotated[
    str, BeforeValidator(normalize_string_or_integer)
]
type NapCatId = NapCatStringInteger


def to_json_value(value: object) -> JsonValue:
    """将 API 边界对象收窄为 JSON 可序列化值。"""
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if isinstance(value, BaseModel):
        dumped = value.model_dump(mode="json", by_alias=True, exclude_none=True)
        return cast(JsonObject, dumped)
    if isinstance(value, list):
        items = cast(list[object], value)
        return [to_json_value(item) for item in items]
    if isinstance(value, tuple):
        items = cast(tuple[object, ...], value)
        return [to_json_value(item) for item in items]
    if isinstance(value, dict):
        result: JsonObject = {}
        raw_items = cast(dict[object, object], value)
        for key, item in raw_items.items():
            if not isinstance(key, str):
                raise TypeError(f"JSON 对象键必须是字符串，实际为: {key!r}")
            result[key] = to_json_value(item)
        return result
    raise TypeError(f"无法序列化为 JSON 协议值: {type(value).__name__}")
