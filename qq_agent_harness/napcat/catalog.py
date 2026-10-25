"""与代码一起发布的锁定上游动作清单，供控制台说明覆盖边界。"""

from importlib.resources import files

from ..contracts import JSON_OBJECT, JsonObject


def action_coverage() -> JsonObject:
    return JSON_OBJECT.validate_json(
        files(__package__).joinpath("actions.json").read_text(encoding="utf-8")
    )
