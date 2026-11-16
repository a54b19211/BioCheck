"""One OpenAI-compatible JSON chat call with a single validation retry (SPEC §2), sent with the openai client."""
import json
import os
import time
import types
import typing

import httpx
import openai
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


def request_options(provider, thinking):
    """The provider's per-call thinking switch (sent as extra body fields), temperature 0 when not thinking."""
    extra = {}
    if provider == "deepseek":
        extra["thinking"] = {"type": "enabled" if thinking else "disabled"}
    elif provider == "vllm":  # local vLLM serving Qwen reads the switch from its chat template
        extra["chat_template_kwargs"] = {"enable_thinking": bool(thinking)}
    return ({} if thinking else {"temperature": 0}), extra


def send(api, **request):
    """One request: (choice, decoded body), or (None, error code) when there is no usable answer."""
    try:
        data = api.chat.completions.with_raw_response.create(**request).http_response.json()
    except openai.APITimeoutError:
        return None, "timeout"
    except openai.APIStatusError as error:
        return None, f"http_{error.status_code}"
    except openai.APIError:
        return None, "network_error"
    except ValueError:
        return None, "bad_response"
    choice = (data.get("choices") or [None])[0] if isinstance(data, dict) else None
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        return None, "bad_response"
    return choice, data


def chat_json(stage, system, payload, model_cls, *, max_tokens=4096, thinking=False, client=None, retry=True):
    """Return (parsed object or None, call record). Never raises for model or network failures. Timeouts and
    refusals are retried by the openai client itself (LLM_MAX_RETRIES, default 2); the prompt, the single
    validation retry and the error codes are this service's. `client` is an httpx.Client (tests stub it)."""
    model = os.environ.get("LLM_MODEL", "deepseek-chat")
    api = openai.OpenAI(base_url=os.environ.get("LLM_BASE_URL", "https://api.deepseek.com"),
                        api_key=os.environ.get("LLM_API_KEY", "") or "-",
                        timeout=float(os.environ.get("LLM_TIMEOUT_SECONDS", "180")),
                        max_retries=int(os.environ.get("LLM_MAX_RETRIES", "2")),
                        http_client=client or httpx.Client())
    options, extra = request_options(os.environ.get("LLM_PROVIDER", "deepseek"), thinking)
    call = {"stage": stage, "model": model, "finish_reason": None,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
            "seconds": 0.0, "error": None, "requests": 0}
    messages = [{"role": "system", "content": system + "\n\n只返回一个JSON对象，格式：" + shape(model_cls)},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)}]
    started = time.monotonic()
    try:
        for attempt in range(2 if retry else 1):
            call["requests"] += 1
            choice, data = send(api, model=model, messages=messages, max_tokens=max_tokens,
                                response_format={"type": "json_object"}, extra_body=extra or None, **options)
            if choice is None:
                call["error"] = data
                return None, call
            content = choice["message"].get("content") or ""
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
                again = "\n\n上次输出未通过校验，请修正后重新返回完整JSON：\n" + problem
                messages[1] = {**messages[1], "content": messages[1]["content"] + again}
        return None, call
    finally:
        call["seconds"] = round(time.monotonic() - started, 3)
        if client is None:
            api.close()
