"""Schema 精简必须保持空值、联合分支、引用和业务数据的校验语义。"""

import json
from copy import deepcopy

import pytest
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel

from qq_agent_harness.contracts import JSON_OBJECT, JsonObject, JsonValue, compact_schema, schema
from qq_agent_harness.napcat.arguments import GroupMemberList, MemberInfo, ReadMedia, SendMessage


def assert_equivalent(original: JsonObject, compacted: JsonObject, values: list[JsonValue]) -> None:
    Draft202012Validator.check_schema(original)
    Draft202012Validator.check_schema(compacted)
    before = Draft202012Validator(original, format_checker=FormatChecker())
    after = Draft202012Validator(compacted, format_checker=FormatChecker())
    for value in values:
        assert before.is_valid(value) == after.is_valid(value), value


@pytest.mark.parametrize(
    "model,values",
    [
        (
            ReadMedia,
            [
                {"user_id": "20"},
                {"user_id": None, "message_id": "30"},
                {"user_id": "0"},
                {"user_id": 20},
                {"media_url": ""},
                {"message_id": "30", "indexes": None},
                {"message_id": "30", "indexes": [0, 3]},
                {"message_id": "30", "indexes": ["0"]},
                {"message_id": "30", "offset": -1},
                {"group_avatar": True, "extra": "forbidden"},
            ],
        ),
        (
            GroupMemberList,
            [
                {},
                {"query": None, "role": None},
                {"query": "管理员", "role": "admin"},
                {"query": "名" * 101},
                {"role": "guest"},
                {"limit": 101},
            ],
        ),
        (MemberInfo, [{"user_id": "20"}, {"user_id": ""}, {}, {"user_id": None}]),
        (
            SendMessage,
            [
                {"parts": [{"type": "text", "text": "正文"}]},
                {"parts": [{"type": "image", "url": "https://example.com/image"}]},
                {"parts": [{"type": "reply", "message_id": "20"}]},
                {"parts": [{"type": "text", "text": ""}]},
                {"parts": [{"type": "unknown", "text": "正文"}]},
                {"parts": []},
                {"parts": None},
            ],
        ),
    ],
)
def test_real_tool_schemas_keep_accepted_and_rejected_inputs(
    model: type[BaseModel], values: list[JsonValue]
) -> None:
    original = JSON_OBJECT.validate_python(model.model_json_schema())
    compacted = schema(model)
    assert_equivalent(original, compacted, values)
    assert len(json.dumps(compacted, ensure_ascii=False)) < len(
        json.dumps(original, ensure_ascii=False)
    )


def test_business_keyword_fields_defaults_and_examples_remain_data() -> None:
    business_data: JsonObject = {"title": "报告", "default": None, "required": [], "anyOf": []}
    original: JsonObject = {
        "type": "object",
        "title": "Outer",
        "required": [],
        "default": business_data,
        "examples": [business_data],
        "properties": {
            "title": {"type": "string", "title": "Title", "minLength": 1},
            "default": {"anyOf": [{"type": "string"}, {"type": "null"}], "default": None},
            "required": {"type": "array", "items": {"type": "string", "title": "Item"}},
            "properties": {"type": "object", "properties": {"title": {"type": "integer"}}},
        },
        "additionalProperties": False,
    }
    untouched = deepcopy(original)
    compacted = compact_schema(original)
    assert original == untouched
    assert compacted["default"] == business_data and compacted["examples"] == [business_data]
    properties = compacted["properties"]
    assert isinstance(properties, dict) and set(properties) == {
        "title",
        "default",
        "required",
        "properties",
    }
    assert properties["default"] == {"type": ["string", "null"]}
    assert_equivalent(
        original,
        compacted,
        [{}, {"title": "报告", "default": None}, {"title": ""}, {"required": [1]}, {"unknown": 1}],
    )


@pytest.mark.parametrize(
    "original",
    [
        {"anyOf": [{"type": "string"}, {"type": "integer"}, {"type": "null"}]},
        {
            "anyOf": [
                {"type": "string", "minLength": 2, "maxLength": 4, "pattern": "^a"},
                {"type": "null"},
            ]
        },
        {"anyOf": [{"type": "string", "format": "date"}, {"type": "null"}]},
        {"anyOf": [{"type": "boolean"}, {"type": "null"}]},
        {"anyOf": [{"type": "object"}, {"type": "null"}]},
        {"anyOf": [{"type": "array"}, {"type": "null"}]},
    ],
)
def test_nullable_simple_union_flattens_without_losing_null_or_string_constraints(
    original: JsonObject,
) -> None:
    compacted = compact_schema(original)
    assert "anyOf" not in compacted
    assert_equivalent(
        original,
        compacted,
        [None, "", "a", "ab", "bb", "abcde", "2026-10-28", "bad-date", 3, 3.1, True, {}, []],
    )


@pytest.mark.parametrize(
    "original",
    [
        {"anyOf": [{"enum": ["owner", "admin", None]}, {"type": "null"}]},
        {"anyOf": [{"type": "string", "enum": ["owner", "admin"]}, {"type": "null"}]},
        {"anyOf": [{"type": "number", "minimum": 5}, {"type": "null"}]},
        {
            "anyOf": [
                {"type": "string", "pattern": "^a"},
                {"type": "string", "pattern": "^b"},
                {"type": "null"},
            ]
        },
        {"type": "string", "anyOf": [{"type": "string"}, {"type": "null"}]},
        {"minLength": 4, "anyOf": [{"type": "string", "minLength": 2}, {"type": "null"}]},
        {"anyOf": [{"oneOf": [{"type": "string"}, {"type": "number"}]}, {"type": "null"}]},
        {
            "$defs": {"Name": {"type": "string", "minLength": 2}},
            "anyOf": [{"$ref": "#/$defs/Name"}, {"type": "null"}],
        },
    ],
)
def test_complex_union_branches_and_parent_constraints_are_preserved(original: JsonObject) -> None:
    compacted = compact_schema(original)
    assert "anyOf" in compacted
    assert_equivalent(
        original, compacted, [None, "", "a", "ab", "bb", "owner", "admin", 3, 8, True, {}, []]
    )


def test_schema_keywords_recurse_but_enum_objects_and_oneof_remain_intact() -> None:
    original: JsonObject = {
        "type": "object",
        "$defs": {"Data": {"enum": [{"title": "data", "default": None, "required": []}]}},
        "properties": {
            "payload": {"$ref": "#/$defs/Data", "title": "Payload"},
            "values": {
                "type": "array",
                "items": {"oneOf": [{"type": "integer", "title": "Int"}, {"type": "number"}]},
            },
        },
        "dependentSchemas": {"payload": {"required": ["values"], "title": "Dependency"}},
        "additionalProperties": {"type": "object", "title": "Extra", "required": []},
    }
    compacted = compact_schema(original)
    assert compacted["$defs"] == original["$defs"]
    assert_equivalent(
        original,
        compacted,
        [
            {},
            {"values": [3]},
            {"values": [3.1]},
            {"payload": {"title": "data", "default": None, "required": []}, "values": [3.1]},
            {"other": {}},
            {"other": "bad"},
        ],
    )


@pytest.mark.parametrize("reference", ["#/$defs/Nullable/anyOf/0", "#/$defs/Nullable/%61nyOf/0"])
def test_reference_into_nullable_branch_keeps_its_json_pointer_target(reference: str) -> None:
    original: JsonObject = {
        "$defs": {"Nullable": {"anyOf": [{"type": "string"}, {"type": "null"}]}},
        "allOf": [{"$ref": reference}],
    }
    compacted = compact_schema(original)
    assert_equivalent(original, compacted, [None, "", "文本", 2, {}, []])
