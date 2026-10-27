"""会话上下文的只读投影：供控制台查看可见内容，不含推理与媒体原始数据。"""

import json
import re

from pydantic import TypeAdapter

from .context import visible_data
from .contracts import JSON_OBJECT, JsonObject, JsonValue
from .providers import ModelSession

ANY_JSON: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)
# 消息段、工具参数或结果里可能夹带 base64；只保留前缀，数据本身不进入接口响应。
INLINE_DATA = re.compile(r"(base64://|data:[\w.+-]+/[\w.+-]+;base64,)[A-Za-z0-9+/=_-]+")
FALLBACK_LABELS = {
    "wake": "唤醒输入",
    "history": "历史消息",
    "knowledge": "知识库",
    "notice": "运行说明",
    "boundary": "轮次结束记录",
    "background": "背景资料",
}


def scrub(value: JsonValue) -> JsonValue:
    if isinstance(value, str):
        return INLINE_DATA.sub(r"\1[内嵌数据已省略]", value)
    if isinstance(value, list):
        return [scrub(item) for item in value]
    if isinstance(value, dict):
        return {key: scrub(item) for key, item in value.items()}
    return value


def _parse(text: JsonValue) -> JsonObject | None:
    if not isinstance(text, str):
        return None
    try:
        return JSON_OBJECT.validate_json(text)
    except ValueError:
        return None


def _body(text: JsonValue) -> str:
    """运行器写入的条目首行是说明，正文在其后。"""
    value = text if isinstance(text, str) else ""
    return value.partition("\n")[2] or value


def _expand(value: JsonValue) -> JsonValue:
    """展开嵌在字符串里的 JSON，切换模型前的资料才不会层层转义。"""
    if isinstance(value, str):
        if value.startswith(("{", "[")):
            try:
                return _expand(ANY_JSON.validate_json(value))
            except ValueError:
                pass
        return value
    if isinstance(value, list):
        return [_expand(item) for item in value]
    if isinstance(value, dict):
        return {key: _expand(item) for key, item in value.items()}
    return value


def _transfer(content: JsonValue) -> str:
    body = _body(content)
    try:
        return json.dumps(_expand(ANY_JSON.validate_json(body)), ensure_ascii=False, indent=2)
    except ValueError:
        return body


def _text(content: JsonValue) -> str:
    """字符串内容，或内容片段中的 text / output_text / refusal。"""
    if isinstance(content, str):
        return content
    parts: list[str] = []
    for part in content if isinstance(content, list) else []:
        if isinstance(part, dict):
            parts.extend(
                value for key in ("text", "refusal") if isinstance(value := part.get(key), str)
            )
    return "".join(parts)


def _arguments(value: JsonValue) -> JsonValue:
    if not isinstance(value, str):
        return value
    try:
        return ANY_JSON.validate_json(value)
    except ValueError:
        return value


def _message(value: JsonValue) -> JsonValue:
    # 会话本身已标明来源，消息内不再重复 scope。
    if isinstance(value, dict):
        return {key: item for key, item in value.items() if key != "scope"}
    return value


def _media(content: JsonValue) -> list[JsonValue]:
    """只给类型、格式和估算体积；data URL 与 base64 不返回。"""
    media: list[JsonValue] = []
    for part in content if isinstance(content, list) else []:
        if not isinstance(part, dict):
            continue
        if part.get("type") in ("image_url", "input_image"):
            url = part.get("image_url")
            if isinstance(url, dict):
                url = url.get("url")
            header, _, encoded = (url if isinstance(url, str) else "").partition(",")
            media.append(
                {
                    "type": "image",
                    "mime": header.removeprefix("data:").split(";")[0],
                    "bytes": len(encoded) * 3 // 4,
                }
            )
        elif part.get("type") == "input_audio" and isinstance(
            audio := part.get("input_audio"), dict
        ):
            encoded = audio.get("data")
            media.append(
                {
                    "type": "audio",
                    "mime": "audio/" + str(audio.get("format") or ""),
                    "bytes": len(encoded) * 3 // 4 if isinstance(encoded, str) else 0,
                }
            )
    return media


def _is_result(item: JsonObject) -> bool:
    return item.get("role") == "tool" or item.get("type") == "function_call_output"


def _result_id(item: JsonObject) -> JsonValue:
    return item.get("tool_call_id") if item.get("role") == "tool" else item.get("call_id")


def _result(item: JsonObject) -> JsonValue:
    content = item.get("content") if item.get("role") == "tool" else item.get("output")
    return _parse(content) or {
        "status": "unknown",
        "message": "结果格式无法解析",
        "preview": content if isinstance(content, str) else "",
    }


def _call(
    base: JsonObject, call_id: JsonValue, name: JsonValue, arguments: JsonValue
) -> JsonObject:
    return {
        **base,
        "kind": "call",
        "id": call_id,
        "name": name,
        "arguments": _arguments(arguments),
        "result": None,
        "result_at": None,
    }


def _output(item: JsonObject, base: JsonObject) -> list[JsonObject]:
    """统一两种协议的模型输出；推理条目和推理字段不进入视图。"""
    kind = item.get("type")
    if kind is None:
        # Chat Completions 的 assistant 消息：正文与工具调用在同一条目内。
        entries: list[JsonObject] = []
        text = "\n".join(
            value
            for value in (_text(item.get("content")), _text(item.get("refusal")))
            if value.strip()
        )
        if text:
            entries.append({**base, "kind": "assistant", "text": text})
        calls = item.get("tool_calls")
        for call in calls if isinstance(calls, list) else []:
            if isinstance(call, dict) and isinstance(function := call.get("function"), dict):
                entries.append(
                    _call(base, call.get("id"), function.get("name"), function.get("arguments"))
                )
        return entries
    if kind == "message":
        text = _text(item.get("content"))
        return [{**base, "kind": "assistant", "text": text}] if text.strip() else []
    if kind == "function_call":
        return [_call(base, item.get("call_id"), item.get("name"), item.get("arguments"))]
    if kind == "reasoning":
        return []
    # 未知的协议项目只标出类型，不展示内容。
    return [{**base, "kind": "other", "label": str(kind), "text": ""}]


def _entry(kind: str, content: JsonValue) -> JsonObject:
    if kind == "system":
        return {"kind": "system", "text": _text(content)}
    if kind == "summary":
        return {"kind": kind, "text": _body(content)}
    if kind == "transfer":
        return {"kind": kind, "text": _transfer(content)}
    if kind == "media":
        return {"kind": "media", "items": _media(content)}
    if kind == "boundary" and (record := _parse(_body(content))) is not None:
        return {**record, "kind": "boundary"}
    data = _parse(content)
    if data is not None:
        if kind == "wake" and isinstance(messages := data.get("当前唤醒消息"), list):
            return {
                "kind": "wake",
                "messages": [_message(message) for message in messages],
                "notice": data.get("历史限制") or "",
            }
        if kind == "knowledge" and isinstance(files := data.get("当前知识库"), list):
            return {
                "kind": "knowledge",
                "files": [
                    {"path": file.get("文件"), "text": file.get("资料")}
                    for file in files
                    if isinstance(file, dict)
                ],
            }
        if kind == "notice" and isinstance(errors := data.get("media_errors"), list):
            return {"kind": "notice", "messages": errors}
    # 格式不符或未登记类别的条目只给可见文本，标签保留原类别便于辨认。
    return {
        "kind": "other",
        "label": FALLBACK_LABELS.get(kind, ""),
        "text": content
        if isinstance(content, str)
        else json.dumps(visible_data(content), ensure_ascii=False),
    }


def _kind(item: JsonObject) -> str:
    """没有标注的条目按协议结构归类。"""
    if item.get("role") == "system":
        return "system"
    if _is_result(item):
        return "tool_result"
    if item.get("role") == "assistant" or item.get("type") in ("message", "function_call"):
        return "output"
    if item.get("type") == "reasoning":
        return "reasoning"
    return "other"


def project(session: ModelSession) -> list[JsonObject]:
    """按上下文原顺序给出可见条目；工具结果按调用 ID 挂到对应调用上。"""
    results: dict[str, tuple[JsonValue, JsonValue]] = {}
    for item in session.items:
        if _is_result(item) and isinstance(call_id := _result_id(item), str):
            note = session.note(item)
            results[call_id] = (_result(item), note.at if note else None)
    entries: list[JsonObject] = []
    attached: set[str] = set()
    for item in session.items:
        note = session.note(item)
        base: JsonObject = {"turn": note.turn if note else None, "at": note.at if note else None}
        kind = note.kind if note else _kind(item)
        if kind == "reasoning":
            continue
        if kind == "output":
            for entry in _output(item, base):
                if isinstance(call_id := entry.get("id"), str) and call_id in results:
                    entry["result"], entry["result_at"] = results[call_id]
                    attached.add(call_id)
                entries.append(entry)
        elif kind == "tool_result":
            call_id = _result_id(item)
            if not (isinstance(call_id, str) and call_id in attached):
                entries.append(
                    {**base, "kind": "result", "call_id": call_id, "result": _result(item)}
                )
        elif (
            kind == "history"
            and (data := _parse(item.get("content"))) is not None
            and isinstance(messages := data.get("历史消息"), list)
        ):
            # 模型按批次接收历史；控制台仍逐条展示，沿用现有消息视图。
            entries.extend(
                {**base, "kind": "history", "message": _message(message)} for message in messages
            )
        else:
            entry = {**base, **_entry(kind, item.get("content"))}
            if kind == "summary" and note is not None:
                # 标出摘要由轮内还是闲时压缩生成，以及生成摘要的模型。
                entry["origin"] = note.origin
                entry["model"] = note.model
            entries.append(entry)
    return [cleaned for entry in entries if isinstance(cleaned := scrub(entry), dict)]
