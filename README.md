# BioCheck

维护人：潘倩。

BioCheck 是 BioRadar 的一个专业服务：根据雷达给的固定材料写一份中文研究回答，然后**逐句**核对这份稿子是否被它引用的原文支持，最后只对明确有错的句子做一次有限修订。材料检索、版本、预算和报告保存都由雷达负责；BioCheck 只看本次请求里给的材料，不联网补事实。

## 运行服务

Python 3.10+。

```sh
pip install -r requirements.txt
export LLM_PROVIDER=deepseek             # deepseek 或 openai（OpenAI 兼容接口）
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=sk-...
export LLM_MODEL=deepseek-chat
export LLM_TIMEOUT_SECONDS=180           # 可选，默认 180
export LLM_WRITE_THINKING=false          # 可选，只影响 generate；核验与修订总是关闭思考
export SERVICE_TOKEN=一个足够长的随机串
uvicorn app.service:app --host 127.0.0.1 --port 18562
```

`GET /health` 不需要令牌；`/v1/*` 都要请求头 `Authorization: Bearer $SERVICE_TOKEN`。没有配置 `SERVICE_TOKEN` 时服务拒绝所有 `/v1/*` 请求（503）。

不想起 HTTP 时可以用 `python -m app.cli verify request.json`（请求体与 HTTP 相同，会真实调用模型）。

## 三个接口（报告的生成、核验、修订）

三个接口共用同一份材料格式（编号由雷达分配，稿子里只写 `[E1]`、`[A1]` 这样的引用）：

```json
{"question": "DREAMM-2两个剂量组的ORR是多少？",
 "evidence": [{"id": "E1", "title": "DREAMM-2 final analysis", "source": "pubmed",
               "published_at": "2020-01-01", "source_kind": "research", "verified": true,
               "text": "In the 2.5 mg/kg arm ORR was 32%. In the 3.4 mg/kg arm ORR was 35%."}],
 "analysis_results": [],
 "source_updates": [],
 "research_scope": {"checked": ["pubmed"], "not_checked": ["专利库"], "failed": []},
 "previous_report": null,
 "max_llm_calls": 20}
```

- `source_kind` 取 `official|research|news|social|registry|company`；`verified` 必填。
- `analysis_results`：`{"id": "A1", "method": "...", "output": {...}, "calculation_basis": {...}, "limitations": [...]}`。
- `source_updates`：`{"target": "E2", "replacement": "E5", "kind": "correction|retraction"}`，表示 E2 已被 E5 更正或撤回。
- `max_llm_calls`（默认 20）：本次最多调用几次模型，用完后剩下的句子判为待核（`unchecked`）。

**POST /v1/generate** → `{"draft": "……", "llm_calls": [...]}`

**POST /v1/verify**（再加 `"draft"`）。例：`draft` 为 `"2.5 mg/kg组ORR为32%[E1]。3.4 mg/kg组ORR为32%[E1]。"`，返回（节选）：

```json
{"status": "FAIL",
 "claims": [
  {"index": 1, "text": "2.5 mg/kg组ORR为32%[E1]。", "start": 0, "end": 22, "citations": ["E1"],
   "verdict": "supported", "labels": [],
   "quotes": [{"ref": "E1", "quote": "In the 2.5 mg/kg arm ORR was 32%", "found": true}], "reason": "与原文一致"},
  {"index": 2, "text": "3.4 mg/kg组ORR为32%[E1]。", "start": 22, "end": 44, "citations": ["E1"],
   "verdict": "contradicted", "labels": ["NUM"],
   "quotes": [{"ref": "E1", "quote": "In the 3.4 mg/kg arm ORR was 35%", "found": true}], "reason": "3.4 mg/kg组ORR为35%"}],
 "coverage": {"verdict": "complete", "reason": "……"},
 "read_requests": [],
 "llm_calls": [{"stage": "verify_claims", "model": "deepseek-chat", "finish_reason": "stop",
                "usage": {"prompt_tokens": 1200, "completion_tokens": 180, "total_tokens": 1380},
                "seconds": 3.2, "error": null},
               {"stage": "verify_coverage", "...": "..."}]}
```

处理过程：

1. 程序拆句（句号、问号、换行；紧跟在句号后面的 `[E1]` 归前一句），按 1..n 编号。
2. 没有引用的句子直接 `insufficient`（`no_citation`）；引用了不存在的编号的句子标 `unknown_ref`，整份 `FAIL`。
3. 其余句子每批最多 4 句、同一段，连同所引原文发给模型。模型按句号回答 `verdict / labels / quotes / reason`。
4. 程序在原文里查找每条短引：找不到（`quote_not_found`）、出现多次（`quote_ambiguous`）、引了本句没引的来源（`quote_outside_citation`）、supported 却缺某个引用的短引、或有不在原文中又没解释的数字（`numeric_unexplained`），都把这一句降为 `insufficient`。
5. 来源规则（程序检查）：引用了已被更正/撤回的来源，除非句子明确写成“当时……，后更正……”并同时引用两端，否则不能当作当前事实；引用 `social` 或 `verified=false` 的来源，句子里须同时有转述（网传/据称/帖子称）和未证实限定，或写成待验证的假设，或只叙述删帖/辟谣/处罚事件。
6. 最后一次调用检查整稿覆盖（是否回答问题、漏了什么重要结果或反证）。

`labels` 只用六类：OBJ 对象/组别，NUM 数值单位分母，TIME 时间，COND 人群/研究条件，STR 推断强度/因果，REF 引用是否支持。

状态：任一 `contradicted` 或引用了不存在的编号 → `FAIL`；任一 `insufficient`/待核，或 `coverage` 不是 `complete` → `REVIEW`；否则 `PASS`。PASS 只表示本次机器检查没发现问题，不是独立准确率。

`read_requests: [{"ref": "E2", "reason": "..."}]` 告诉雷达该补读哪份材料：缺支持的句子 → 补读它引用的 E 编号；被更正的来源但更正通知不在材料里 → 补读更正通知的编号。

批次失败（模型两次都没给出合格 JSON、超时等）只影响这一批的句子（`batch_failed`），其余照常。

**POST /v1/revise**（再加 `"draft"` 和上一步的 `"review"`）→ `{"draft": "……", "changed": [{"index": 2, "before": "……", "after": "……"}], "llm_calls": [...]}`

- 只改两类句子：`contradicted` 且有找到的短引的句子（交给模型，每句换成一句，只能用原句自己的引用；不合格的替换直接丢弃，原句不动）；核验时被标出 `scope_span` 的“未报告写成未实施”的句子（程序直接改成“本次引用材料不足以确认“……”这一说法”，不调用模型）。
- `review` 必须是对同一份 `draft` 的核验结果，否则返回 409 `review_mismatch`。
- 改完后由雷达再调一次 `/v1/verify`。

错误统一为 `{"error_code": "...", "message": "..."}`：401 `unauthorized`、400 `invalid_request`/`no_materials`/`duplicate_id`、409 `review_mismatch`、502 生成失败（附 `llm_calls`）。

## 抽取一致性接口（v3 6.2）

**POST /v1/extraction-consistency**（契约 `extraction_consistency.v1`）。这个接口只核对加工产物和它取自的原文是否对应，不判断事实真伪，也不调模型，`llm_calls` 恒为 `[]`。

请求是严格格式：多一个字段、写错字段名、某类条目带了不该带的字段，或者 `end - start` 不等于原文长度，都返回 400 `invalid_request`，不会返回 200 冒充已检查。

```json
{"subject": {"source_id": "nmpa", "record_id": "…", "version_id": "…", "raw_sha256": "<64位>",
             "artifact": {"processor_version": "datafabric.clean_text.v11", "text_sha256": "<64位>"}},
 "passage": {"start": 1000, "end": 1093, "text": "<从固定产物切出的 [start,end) 原文>"},
 "items": [{"id": "m1", "kind": "measurement", "producer": {"name": "…", "version": "…"},
            "locator": {"start": 1040, "end": 1045}, "quote": "100mg",
            "fields": {"value": "100", "unit": "mg"}}]}
```

- `kind` 和它允许的 `fields`：
  - `identifier`：只能带 `identifier`（`{scheme, raw, value}`，即原文写法和生产者规范化后的写法）；
  - `measurement`：必须带 `value`，可选 `unit`、`denominator`（数字或 `unknown`）、`numerator`、`period`、`population`；
  - `claim`：可带以上数值字段，以及 `label`；
  - `annotation`：不带字段，只核对定位和短引。
- `locator` 用产物里的绝对偏移，`quote` 必须正好是定位处的文字。定位不对时，其余字段一律不读，记 `unsupported`。
- 每个字段的结果是以下四种之一：
  - `match`：对上了；
  - `mismatch`：原文写的是别的；
  - `not_found`：原文里找不到；
  - `unsupported`：规则判断不了，例如单位不在单位表里、`label` 属于语义判断、比例关系无法用规则核对。
- 条目状态：所有字段都是 `match` 才算 `consistent`；只要有 `mismatch` 或 `not_found` 就是 `inconsistent`；其余（含任何 `unsupported`）都是 `unchecked`。`unchecked` 不能当作已检查通过。
- 规则：数值按 `/v1/verify` 的舍入规则核对；单位用 `app/extraction.py` 里带版本的固定单位表；分母识别 `n/N`、`n of N`、"N例中…n" 和 `N=`、"共N" 这几种写法；时间和人群只做字面核对。改动规则或单位表时，必须同时提升 `checker_version`。

返回：

```json
{"contract_version": "extraction_consistency.v1", "checker_version": "biocheck.extraction_rules.v1",
 "subject": {…},
 "items": [{"id": "m1", "kind": "measurement", "status": "consistent",
            "fields": {"locator": {"result": "match", "span": [1040, 1045]}, "quote": {…},
                       "value": {…}, "unit": {…}}}],
 "counts": {"consistent": 1, "inconsistent": 0, "unchecked": 0}, "llm_calls": []}
```

## 运行测试

```sh
python -m pytest -q
```

测试不联网，用 `tests/conftest.py` 里的 `FakeLLM` 代替模型。`evaluation/` 下保留 `tasks.json`（题目及 Fabric 记录版本）、`controls.json` 和 `regressions.json`（正确/错误对照句），供以后用真实材料评测。

## 这次重写改了什么

上一轮验收中，逐句核验开着 DeepSeek 思考模式，12k–16k 的输出额度全部用在思考上，正文为空；再加上“服务把下一次模型请求交回雷达、雷达代调再回传回执”的反向协议，以及指纹、哈希、结构纠错重放，整条链很脆。这次改成普通的同步 HTTP 服务，自己调模型；核验、覆盖和修订一律关闭思考，`max_tokens` 4096（写稿 8192）；模型只按程序给的句号回答，一句答错只影响这一句。删除了 rpc.py、合约版本与哈希、复用缓存、整稿旧核验、规划步骤、结构纠错重放和 support_audit（并入短引检查）。代码从约 2,900 行减到约 730 行。

## 已知限制

- 短引只做逐字查找（空白合并），模型稍有改写就会判 `quote_not_found`，结果偏保守（多出 REVIEW）。
- 社媒归因、历史回顾、范围修订的措辞检查靠关键词，可能漏判少见说法；实质语义仍由模型判断。
- 分析结果按整段 JSON 文本查短引，不再按 output 路径定位；同一数字出现在不同字段时由模型和短引唯一性共同把关。
- 每批都会重发所引原文全文，长材料较费 token。
- 覆盖检查只看到材料标题和逐句结论，看不到原文全文，可能漏报“遗漏了某个重要结果”。
- 还没有用真实模型跑过新版本的评测；生成/核验共用同一模型，不能代替独立的人工专业标注。
