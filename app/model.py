"""One configurable model caller. The research functions accept any callable with this interface."""
import json
import os
import time
import urllib.error
import urllib.request
from pathlib import Path


def read_env(path=".env"):
    values = {}
    if Path(path).exists():
        for line in Path(path).read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                values[key.strip()] = value.strip().strip("\"'")
    return values | dict(os.environ)


class DeepSeek:
    def __init__(self, output, *, model=None, max_calls=None):
        config = read_env()
        self.key = config["DEEPSEEK_API_KEY"]
        self.url = config.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")
        self.model = model or config.get("DEEPSEEK_MODEL", "deepseek-v4-pro")
        self.output = Path(output)
        self.output.mkdir(parents=True, exist_ok=True)
        self.max_calls = max_calls
        self.calls = []

    def __call__(self, stage, instruction, payload):
        if self.max_calls is not None and len(self.calls) >= self.max_calls:
            raise RuntimeError("本次模型调用额度用完；未执行的核验不能记为通过")
        thinking = stage in {"verify", "revise"}
        request = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": instruction},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            "response_format": {"type": "json_object"},
            "thinking": {"type": "enabled" if thinking else "disabled"},
            "max_tokens": 24000 if thinking else 12000,
        }
        if thinking:
            request["reasoning_effort"] = "high"
        else:
            request["temperature"] = 0
        record = {"stage": stage, "request": request, "status": "sent"}
        self.calls.append(record)
        path = self.output / f"{len(self.calls):03d}-{stage}.json"
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
        started = time.monotonic()
        try:
            req = urllib.request.Request(
                self.url + "/chat/completions",
                data=json.dumps(request, ensure_ascii=False).encode(),
                headers={"Authorization": "Bearer " + self.key, "Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=240) as response:
                result = json.load(response)
            choice = result["choices"][0]
            record.update(response_id=result.get("id"), model=result.get("model"),
                          usage=result.get("usage", {}), finish_reason=choice.get("finish_reason"),
                          content=choice["message"].get("content"))
            if choice.get("finish_reason") != "stop":
                raise RuntimeError("模型返回未完整结束：" + str(choice.get("finish_reason")))
            value = json.loads(choice["message"]["content"])
            if not isinstance(value, dict):
                raise ValueError("模型结果应为JSON对象")
            record["status"] = "received"
            return value
        except Exception as error:
            # No hidden retries: a timed-out response can still have consumed tokens.
            record.update(status="error", error=type(error).__name__)
            raise RuntimeError(f"{stage} 模型调用失败（{type(error).__name__}），详见本次调用记录") from error
        finally:
            record["seconds"] = round(time.monotonic() - started, 3)
            # Deliberately retain only public output, never reasoning_content or HTTP credentials.
            path.write_text(json.dumps(record, ensure_ascii=False, indent=2))
