"""One OpenAI-compatible JSON chat call with a single validation retry (SPEC §2). The HTTP side is llm_transport."""
import json
import os
import time
import types
import typing

import httpx
import llm_transport as lt
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


def chat_json(stage, system, payload, model_cls, *, max_tokens=4096, thinking=False, client=None, retry=True):
    """Return (parsed object or None, call record). Never raises for model or network failures. The HTTP side
    (request body, sending, request count) is the shared llm_transport; the prompt, the single validation retry and
    the error codes are this service's."""
    provider_name = os.environ.get("LLM_PROVIDER", "deepseek")
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    provider = lt.Provider(
        kind=provider_name if provider_name in ("deepseek", "vllm") else "openai_compatible",
        base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"), model=model,
        api_key=os.environ.get("LLM_API_KEY", ""), timeout_seconds=float(os.environ.get("LLM_TIMEOUT_SECONDS", "180")))
    call = {"stage": stage, "model": model, "finish_reason": None,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "seconds": 0.0, "error": None, "requests": 0}
    messages = [{"role": "system", "content": system + "\n\n只返回一个JSON对象，格式：" + shape(model_cls)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    record = lt.CallRecord(stage=stage, endpoint_id="", model=model)
    own_client = client is None
    client = client or httpx.Client()
    started = time.monotonic()
    try:
        for attempt in range(2 if retry else 1):
            body = lt.build_body(provider, messages, max_tokens=max_tokens, thinking=thinking)
            sent = lt.send(provider, body, record, client=client)
            call["requests"] = record.requests
            if sent.exception is not None:
                call["error"] = "timeout" if sent.timed_out else "network_error"  # a timeout is not resent: billed?
                return None, call
            if sent.status != 200:
                call["error"] = f"http_{sent.status}"
                return None, call
            answer = sent.envelope()
            if answer.problem:
                call["error"] = "bad_response"
                return None, call
            content = answer.content or ""
            for key, value in answer.usage.items():
                if key in call["usage"] and isinstance(value, int):
                    call["usage"][key] += value
            call["finish_reason"] = answer.finish_reason
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
                again = "\n\n上次输出未通过校验，请修正后重新返回完整JSON：\n" + problem
                messages[1] = {**messages[1], "content": messages[1]["content"] + again}
        return None, call
    finally:
        call["seconds"] = round(time.monotonic() - started, 3)
        if own_client:
            client.close()
