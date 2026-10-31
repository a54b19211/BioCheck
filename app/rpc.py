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
    "plan": ({"packet"}, {"method", "max_plan_retries"}),
    "write": ({"packet"}, {"method", "plan"}),
    "generate": ({"packet"}, {"method", "max_plan_retries"}),
    "verify": ({"packet", "draft"}, {"max_verify_retries", "mode", "previous_review"}),
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
    names = ("core.py", "claims.py", "contracts.py", "evidence.py", "prompts.py", "analysis.py", "source_context.py", "rpc.py", "service.py")
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
        "features": ["versioned_read_requests", "source_updates", "unconfirmed_source_scope",
                     "full_draft_audit_binding", "revision_requires_verification", "bounded_plan_repair", "bounded_verify_contract_repair"],
        "plan_retry_policy": {"argument": "max_plan_retries", "default": 0, "maximum": 1,
                              "stages": ["replan_plain", "replan_bound"],
                              "diagnostic_limit": core.PLAN_DIAGNOSTIC_LIMIT},
        "verify_retry_policy": {"argument": "max_verify_retries", "default": 0, "maximum": 1,
                                "stages": ["verify_repair"], "diagnostic_limit": 8},
        "paragraph_verification": {
            "argument": "mode", "default": "whole", "value": "paragraph",
            "grain": "cited_claims", "maximum_claims_per_batch": 3, "maximum_batch_bytes": 120000,
            "stages": {"verify_claim": 12288, "verify_claim_retry": 16384, "verify_coverage": 8192,
                       "revise_claims": 16384},
            "max_model_calls": "2 * claim_count + 1 (upper bound; actual requests batch up to 3 claims)",
            "previous_review": "optional full same-method review; exact claim/context/source binding required",
            "receipt_fields": ["call_id", "request_hash", "finish_reason", "output"],
            "retry_on": "length", "max_retries_per_batch": 1,
            "format_retry": False, "unknown_result": "recover_same_call_before_dispatch"},
    }


def _model_call_limit(operation, arguments):
    if operation == "verify" and arguments.get("mode") == "paragraph":
        draft = arguments.get("draft")
        from .claims import sentences
        return 2 * len(sentences(draft)) + 1 if isinstance(draft, str) else 0
    if operation == "revise" and arguments.get("review", {}).get("verification_method") == "cited_claims":
        return 1
    return MAX_MODEL_CALLS


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
    for field in ("packet", "review", "previous_review"):
        if field in arguments and not isinstance(arguments[field], dict):
            raise ProtocolError("invalid_arguments", "packet/review必须为JSON对象")
    if "audit" in arguments and arguments["audit"] is not None and not isinstance(arguments["audit"], dict):
        raise ProtocolError("invalid_arguments", "audit必须为对象或null")
    if "source_errors" in arguments and not isinstance(arguments["source_errors"], list):
        raise ProtocolError("invalid_arguments", "source_errors必须为数组")
    for name in ("max_plan_retries", "max_verify_retries"):
        if name in arguments and (type(arguments[name]) is not int or arguments[name] not in (0, 1)):
            raise ProtocolError("invalid_arguments", name + "只允许0或1")
    receipts = request.get("model_receipts", [])
    if not isinstance(receipts, list) or len(receipts) > _model_call_limit(operation, arguments):
        raise ProtocolError("invalid_receipts", "model_receipts须为有界数组")
    version = request.get("provider_version")
    if receipts and version is None:
        raise ProtocolError("provider_version_required", "续轮必须固定provider_version")
    if version is not None and version != PROVIDER_VERSION:
        raise ProtocolError("provider_version_mismatch", "方法版本已变化，不能混用先前回执")
    paragraph_mode = (operation == "verify" and arguments.get("mode") == "paragraph"
                      or operation == "revise" and arguments.get("review", {}).get("verification_method") == "cited_claims")
    fields = {"call_id", "request_hash", "output"} | ({"finish_reason"} if paragraph_mode else set())
    seen = set()
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != fields:
            raise ProtocolError("invalid_receipt", "回执字段不符合该操作约定；分段核验须有finish_reason")
        for field in ("call_id", "request_hash"):
            value = receipt[field]
            if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
                raise ProtocolError("invalid_receipt", "回执请求标识格式无效")
        if receipt["call_id"] in seen:
            raise ProtocolError("receipt_conflict", "回执重复")
        if paragraph_mode:
            finish = receipt["finish_reason"]
            if finish not in ("stop", "length"):
                raise ProtocolError("invalid_receipt", "未知结束状态不能用于重发或宣布核验完成")
            if finish == "length" and receipt["output"] is not None and not isinstance(receipt["output"], (str, dict)):
                raise ProtocolError("invalid_receipt", "截断回执只保留公开原文、JSON对象或null")
            if finish == "stop" and not isinstance(receipt["output"], dict):
                raise ProtocolError("invalid_receipt", "stop回执须有已解析的JSON对象；解析失败不自动重发")
        elif not isinstance(receipt["output"], dict):
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
        paragraph_mode = (operation == "verify" and arguments.get("mode") == "paragraph"
                      or operation == "revise" and arguments.get("review", {}).get("verification_method") == "cited_claims")
        call_limit = _model_call_limit(operation, arguments)

        def call(stage, instruction, payload):
            nonlocal used
            if used >= call_limit:
                raise ProtocolError("model_call_limit", "专业操作超过模型步骤上限")
            if not isinstance(stage, str) or not isinstance(instruction, str) or not isinstance(payload, dict):
                raise ProtocolError("invalid_model_request", "方法产生的模型请求无效")
            model_request = {"stage": stage, "instruction": instruction, "payload": copy.deepcopy(payload),
                             "schema": None}
            if paragraph_mode:
                model_request["max_output_tokens"] = payload["verification_request"]["max_output_tokens"]
            request_hash = digest(model_request)
            binding = {"operation_hash": operation_hash, "sequence": used, "request_hash": request_hash}
            if paragraph_mode:
                binding["receipt_prefix_hash"] = digest(receipts[:used])
            call_id = digest(binding)
            model_call = dict(model_request, call_id=call_id, request_hash=request_hash)
            if used == len(receipts):
                raise ModelRequired(model_call)
            receipt = receipts[used]
            if receipt["call_id"] != call_id or receipt["request_hash"] != request_hash:
                raise ProtocolError("receipt_conflict", "回执与固定输入、操作、阶段或调用次序不一致")
            used += 1
            if paragraph_mode:
                return {"finish_reason": receipt["finish_reason"], "output": copy.deepcopy(receipt["output"])}
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
    except core.VerificationCompletionError as error:
        return dict(reply, status="failed", error_code=error.code, message=str(error),
                    unit_id=error.unit_id, claim_ids=getattr(error, "claim_ids", []),
                    finish_reason=error.finish_reason, attempt=error.attempt)
    except core.VerificationContractError as error:
        attempted = error.repair_attempted
        return dict(reply, status="failed",
                    error_code="verification_repair_exhausted" if attempted else "verification_contract_invalid",
                    message="核验回执结构不合要求，不能据此宣布成稿通过",
                    diagnostics=error.diagnostics, diagnostics_omitted=error.omitted,
                    verify_retry={"eligible": not attempted and error.retry_eligible and not paragraph_mode,
                                  "attempted": attempted, "limit": 1})
    except core.PlanReferenceError as error:
        attempted = error.repair_attempted
        eligible = operation in {"plan", "generate"} and not attempted
        return dict(reply, status="failed",
                    error_code="plan_repair_exhausted" if attempted else "plan_reference_invalid",
                    message="计划引用校验失败，未进入后续写作；原回执保持不变",
                    diagnostics=error.diagnostics, diagnostics_omitted=error.omitted,
                    plan_retry={"eligible": eligible, "attempted": attempted, "limit": 1})
    except ProtocolError as error:
        return dict(reply, status="failed", error_code=error.code, message=error.message)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError, RecursionError, OverflowError):
        return dict(reply, status="failed", error_code="method_input_invalid",
                    message="方法输入或公开模型回执不符合当前契约；保留宿主原记录，未自动重试")
