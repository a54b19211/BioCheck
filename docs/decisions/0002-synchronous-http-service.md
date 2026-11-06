# 0002 A plain synchronous HTTP service that calls the model itself

Status: accepted (recorded 2026-11-06, moved from the README section "这次重写改了什么")

## Context

The earlier BioCheck ran sentence verification with DeepSeek's thinking mode on: the 12k-16k output budget went
entirely to reasoning and the answer came back empty. It also used a reverse protocol - the service handed its
next model request back to the radar, which called the model and returned a receipt - plus fingerprints, hashes
and replays to repair malformed output. The chain was fragile.

## Decision

- BioCheck is an ordinary synchronous HTTP service that calls the model itself (`app/llm.py`).
- Verification, coverage and revision always run with thinking off and `max_tokens` 4096 (writing: 8192, thinking
  per `LLM_WRITE_THINKING`).
- The model answers only per sentence number the program assigned; a wrong answer affects that sentence alone.
- Removed: rpc.py, contract versions and hashes of the verification protocol, the reuse cache, whole-draft legacy
  verification, the planning step, structural replay and support_audit (folded into the short-quote check). The
  code went from about 2,900 to about 730 lines.

## Consequences

- One validation retry per call (with the pydantic error), counted in `max_llm_calls` and in each call's
  `requests`; a batch that still fails marks only its own sentences `batch_failed`.
- No payloads, raw responses, keys or reasoning are stored; a call record keeps stage, model, usage,
  finish_reason, seconds, error and requests.
