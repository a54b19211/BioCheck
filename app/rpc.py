"""Stateless professional steps. Only the host executes and pays for model calls."""
import copy
import hashlib
import json
from pathlib import Path

from . import core

SCHEMA_VERSION = "bioradar.professional.v1"
PROVIDER = "biocheck"
MAX_MODEL_CALLS = 8
OPERATIONS = {
    "plan": ({"packet"}, {"method"}),
    "write": ({"packet"}, {"method", "plan"}),
    "generate": ({"packet"}, {"method"}),
    "verify": ({"packet", "draft"}, set()),
    "audit_support": ({"packet", "review"}, {"targets"}),
    "quality_feedback": ({"review"}, {"audit", "source_errors"}),
    "revise": ({"packet", "review"}, {"audit", "source_errors"}),
}


class ProtocolError(Exception):
    def __init__(self, code, message):
        self.code, self.message = code, message


class ModelRequired(Exception):
    def __init__(self, model_call):
        self.model_call = model_call


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def method_version():
    # Hash executable methods and this transport, never environment files or model credentials.
    folder = Path(__file__).parent
    names = ("core.py", "evidence.py", "prompts.py", "analysis.py", "rpc.py", "service.py")
    code = [(name, (folder / name).read_text()) for name in names if (folder / name).is_file()]
    return digest(code)


PROVIDER_VERSION = method_version()


def capabilities():
    return {
        "schema_version": SCHEMA_VERSION, "provider": PROVIDER,
        "provider_version": PROVIDER_VERSION, "operations": list(OPERATIONS),
        "operation_arguments": {
            name: {"required": sorted(required), "optional": sorted(optional)}
            for name, (required, optional) in OPERATIONS.items()
        },
        "max_model_calls_per_operation": MAX_MODEL_CALLS,
        "model_execution": "host_only",
    }


def _validate(request):
    if not isinstance(request, dict):
        raise ProtocolError("invalid_request", "请求必须为JSON对象")
    allowed = {"schema_version", "operation_id", "operation", "arguments", "provider_version", "model_receipts"}
    if set(request) - allowed or request.get("schema_version") != SCHEMA_VERSION:
        raise ProtocolError("invalid_request", "协议版本或请求字段不符合约定")
    operation_id, operation = request.get("operation_id"), request.get("operation")
    if not isinstance(operation_id, str) or not operation_id.strip() or len(operation_id) > 256:
        raise ProtocolError("invalid_operation_id", "operation_id必须为不超过256字符的非空字符串")
    if not isinstance(operation, str) or operation not in OPERATIONS:
        raise ProtocolError("unsupported_operation", "未支持的专业操作")
    arguments = request.get("arguments")
    required, optional = OPERATIONS[operation]
    if not isinstance(arguments, dict) or required - arguments.keys() or arguments.keys() - required - optional:
        raise ProtocolError("invalid_arguments", "操作参数缺失或包含未知字段")
    for field in ("packet", "review"):
        if field in arguments and not isinstance(arguments[field], dict):
            raise ProtocolError("invalid_arguments", "packet/review必须为JSON对象")
    if "audit" in arguments and arguments["audit"] is not None and not isinstance(arguments["audit"], dict):
        raise ProtocolError("invalid_arguments", "audit必须为对象或null")
    if "source_errors" in arguments and not isinstance(arguments["source_errors"], list):
        raise ProtocolError("invalid_arguments", "source_errors必须为数组")
    receipts = request.get("model_receipts", [])
    if not isinstance(receipts, list) or len(receipts) > MAX_MODEL_CALLS:
        raise ProtocolError("invalid_receipts", "model_receipts须为有界数组")
    version = request.get("provider_version")
    if receipts and version is None:
        raise ProtocolError("provider_version_required", "续轮必须固定provider_version")
    if version is not None and version != PROVIDER_VERSION:
        raise ProtocolError("provider_version_mismatch", "方法版本已变化，不能混用先前回执")
    seen = set()
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != {"call_id", "request_hash", "output"}:
            raise ProtocolError("invalid_receipt", "只接收宿主已完成的call_id/request_hash/output回执")
        for field in ("call_id", "request_hash"):
            value = receipt[field]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ProtocolError("invalid_receipt", "回执请求标识格式无效")
        if receipt["call_id"] in seen:
            raise ProtocolError("receipt_conflict", "回执重复")
        if not isinstance(receipt["output"], dict):
            raise ProtocolError("invalid_receipt", "模型公开输出须为JSON对象")
        seen.add(receipt["call_id"])
    canonical(request)  # Reject non-finite numbers and non-JSON Python inputs before execution.
    return operation, copy.deepcopy(arguments), receipts


def execute(request):
    reply = {"schema_version": SCHEMA_VERSION, "provider": PROVIDER,
             "provider_version": PROVIDER_VERSION,
             "operation_id": request.get("operation_id") if isinstance(request, dict) else None}
    try:
        operation, arguments, receipts = _validate(request)
        operation_hash = digest({"schema_version": SCHEMA_VERSION, "provider_version": PROVIDER_VERSION,
                                 "operation_id": request["operation_id"], "operation": operation,
                                 "arguments": arguments})
        used = 0

        def call(stage, instruction, payload):
            nonlocal used
            if used >= MAX_MODEL_CALLS:
                raise ProtocolError("model_call_limit", "专业操作超过模型步骤上限")
            if not isinstance(stage, str) or not isinstance(instruction, str) or not isinstance(payload, dict):
                raise ProtocolError("invalid_model_request", "方法产生的模型请求无效")
            model_request = {"stage": stage, "instruction": instruction, "payload": copy.deepcopy(payload),
                             "schema": None}
            request_hash = digest(model_request)
            call_id = digest({"operation_hash": operation_hash, "sequence": used,
                              "request_hash": request_hash})
            model_call = dict(model_request, call_id=call_id, request_hash=request_hash)
            if used == len(receipts):
                raise ModelRequired(model_call)
            receipt = receipts[used]
            if receipt["call_id"] != call_id or receipt["request_hash"] != request_hash:
                raise ProtocolError("receipt_conflict", "回执与固定输入、操作、阶段或调用次序不一致")
            used += 1
            return copy.deepcopy(receipt["output"])

        if operation == "audit_support" and arguments.get("targets") is not None:
            targets = arguments["targets"]
            if (not isinstance(targets, list) or any(
                    not isinstance(t, list) or len(t) != 2 or not isinstance(t[0], str)
                    or type(t[1]) is not int for t in targets)):
                raise ProtocolError("invalid_arguments", "targets须为[unit_id,claim_index]数组")
            if len({tuple(t) for t in targets}) != len(targets):
                raise ProtocolError("invalid_arguments", "targets不可重复")
            arguments["targets"] = [tuple(t) for t in targets]
        method = getattr(core, operation)
        result = method(**arguments) if operation == "quality_feedback" else method(call=call, **arguments)
        if used != len(receipts):
            raise ProtocolError("extra_receipts", "操作完成后仍有未消费的回执")
        canonical(result)
        return dict(reply, status="completed", result=result)
    except ModelRequired as needed:
        return dict(reply, status="model_required", model_call=needed.model_call)
    except ProtocolError as error:
        return dict(reply, status="failed", error_code=error.code, message=error.message)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, OverflowError):
        return dict(reply, status="failed", error_code="method_input_invalid",
                    message="方法输入或公开模型回执不符合当前契约；保留宿主原记录，未自动重试")
