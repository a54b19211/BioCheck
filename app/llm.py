"""One OpenAI-compatible JSON chat call with a single validation retry (SPEC §2)."""
import json
import os
import time
import types
import typing

import httpx
from pydantic import BaseModel, ValidationError


def shape(tp):
    """Short field description for the system prompt, e.g. {"index":int,"verdict":"a|b"}."""
    origin, args = typing.get_origin(tp), typing.get_args(tp)
    if isinstance(tp, type) and issubclass(tp, BaseModel):
        return "{" + ",".join(f'"{k}":{shape(f.annotation)}' for k, f in tp.model_fields.items()) + "}"
    if origin is list:
        return "[" + shape(args[0]) + "]"
    if origin is typing.Literal:
        return '"' + "|".join(map(str, args)) + '"'
    if origin in (typing.Union, types.UnionType):
        return "|".join(shape(a) for a in args)
    return {str: "string", int: "int", float: "number", bool: "bool", type(None): "null"}.get(tp, "any")


def chat_json(stage, system, payload, model_cls, *, max_tokens=4096, thinking=False, client=None):
    """Return (parsed object or None, call record). Never raises for model or network failures."""
    provider = os.environ.get("LLM_PROVIDER", "deepseek")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    call = {"stage": stage, "model": model, "finish_reason": None,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "seconds": 0.0, "error": None}
    body = {"model": model, "max_tokens": max_tokens, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": system + "\n\n只返回一个JSON对象，格式：" + shape(model_cls)},
                         {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]}
    if provider == "deepseek":
        body["thinking"] = {"type": "enabled" if thinking else "disabled"}
    if not thinking:
        body["temperature"] = 0
    url = os.environ.get("LLM_BASE_URL", "https://api.deepseek.com").rstrip("/") + "/chat/completions"
    headers = {"Authorization": "Bearer " + os.environ.get("LLM_API_KEY", "")}
    client = client or httpx.Client(timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "180")))
    started = time.monotonic()
    try:
        for attempt in range(2):
            try:
                response = client.post(url, json=body, headers=headers)
            except httpx.TimeoutException:
                call["error"] = "timeout"  # the provider may still bill it; do not resend
                return None, call
            except httpx.HTTPError:
                call["error"] = "network_error"
                return None, call
            if response.status_code != 200:
                call["error"] = f"http_{response.status_code}"
                return None, call
            try:
                data = response.json()
                choice = data["choices"][0]
                content = choice["message"].get("content") or ""
            except (ValueError, KeyError, IndexError, TypeError, AttributeError):
                call["error"] = "bad_response"
                return None, call
            for key, value in (data.get("usage") or {}).items():
                if key in call["usage"] and isinstance(value, int):
                    call["usage"][key] += value
            call["finish_reason"] = choice.get("finish_reason")
            if not content.strip() and call["finish_reason"] == "length":
                call["error"] = "output_empty_after_limit"  # reasoning used the whole budget
                return None, call
            try:
                parsed = model_cls.model_validate_json(content)
                call["error"] = None
                return parsed, call
            except ValidationError as error:
                problem = str(error)
            call["error"] = "invalid_output"
            if attempt == 0:
                body["messages"][1]["content"] += "\n\n上次输出未通过校验，请修正后重新返回完整JSON：\n" + problem
        return None, call
    finally:
        call["seconds"] = round(time.monotonic() - started, 3)
