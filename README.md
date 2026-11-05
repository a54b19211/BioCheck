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

**POST /v1/extraction-consistency**（契约 `extraction_consistency.v1`）。这个接口核对加工产物和它取自的固定原文是否对应：每个字段都写在了它指出的位置，并且这些字段属于同一个数量。它不判断事实真伪，也不调模型，`llm_calls` 恒为 `[]`。

请求格式是严格的。下面任何一种情况都返回 400 `invalid_request`，不会回 200 冒充已检查：
- 多了未知字段，或者少了某类条目的必填维度；
- `stated` 的字段缺少值或位置，或者 `unknown` 的字段却带了值；
- 某段原文的区间与文本长度不符；
- 各段原文的区间互相重叠；
- 位置指向了请求里不存在的段。

```json
{"subject": {"source_id": "nmpa", "record_id": "…", "version_id": "…", "raw_sha256": "<64位>", "is_demo": true,
             "artifact": {"processor_version": "datafabric.clean_text.v11", "text_sha256": "<64位>"}},
 "passages": [{"id": "h", "role": "context", "start": 5200, "end": 5310, "text": "| 序号 | 药品品名 | … |\n| ---- | … |"},
              {"id": "r", "role": "primary", "start": 400156, "end": 400260, "text": "| 954 | 利巴韦林片 | … | 100mg | … |"}],
 "items": [{"id": "m1", "kind": "measurement", "producer": {"name": "…", "version": "…"},
            "locator": {"passage": "r", "start": 400189, "end": 400194}, "quote": "100mg",
            "fields": {"object": {"value": "利巴韦林片", "span": {"passage": "r", "start": …, "end": …}},
                       "metric": {"value": "药品规格", "span": {"passage": "h", "start": …, "end": …}},
                       "value": {"value": "100", "span": {…}}, "unit": {"value": "mg", "span": {…}},
                       "period": {"state": "unknown"}, "scope": {"state": "unknown"},
                       "denominator": {"state": "not_applicable"}, "measure_kind": "stock"}}]}
```

- `passages`：同一份固定产物里有界的几段原文（最多 8 段，合计不超过 2 万字），各自用产物的绝对偏移。不能把不连续的几段拼接成一个伪造的连续区间。表头和数据行分在两段时，证明不了是同一张表，列名会记 `unsupported`；要核对列名，表头和这一行必须同在一段**真实连续**的原文里：从固定产物中原样切出的一个区间 [start, end)，仍受 2 万字上限约束。不能把不连续的切片拼起来当作连续区间。表头离得太远、装不进一段时，就证明不了是同一张表，结果保持 `unsupported`。调用方（Fabric）回读时按同样的标准核对，不能因为这条边界而放宽回读规则。原文由调用方从固定产物回读得到，这里只核对给出的这几段，不代表已经核验了来源原件。
- 每个字段写成 `{"value": …, "span": {"passage", "start", "end"}}`，或者 `{"state": "unknown" | "not_applicable"}`（来源没写）。各类条目的必填维度：
  - `measurement`（语义同 Fabric `Measurement`）：object、metric、value、unit、period、scope、denominator，以及 measure_kind；
  - `identifier`：scheme 和 identifier；
  - `claim`：statement 和 polarity，可带 qualifiers；
  - `annotation`：label。
- 规则：
  - `quote` 必须正好是 `locator` 处的文字，否则其余字段一律不读，记 `unsupported`。
  - value 的 span 必须恰好是原文里一个完整的数字（含符号、小数、千分位），并与所报的值精确相等。只做两种转换：全角数字、千分位逗号。不沿用报告接口的舍入。
  - unit 必须紧跟在这个数字后面，用带版本的单位表取最长匹配，所以 mg/kg、mg/day 不会被读成 mg 或分母。
  - denominator 必须是这个数字所在、并在同一分句里写明的比例的分母（分句以 "；;。！？" 和换行切分）。百分数可以由这样的比例按显示的位数复算。
  - period、scope、object、metric 必须落在数值所在的"组"里，并且这个组里没有别的数量（数值自己比例的分子、分母除外）。组是在分句的基础上再按 "，,、" 切开的一段；表格的一行算一条记录。
  - 表格的列名必须是同一段原文里、数值所在那张表的表头，并且在同一列。表头和数据行分在不同段时，证明不了是同一张表，记 `unsupported`。
  - measure_kind 为 rate 时，必须在数值处写着 %，或者有真实写明的比例；"没写分母"不算证据。
  - identifier 只核对原文的字面写法，需要规范化才对得上的记 `unsupported`。
  - claim 的 polarity 和 qualifiers、annotation 的判断，都属于语义判断，记 `unsupported`。
  - `unknown` 只有在规则能证明来源确实没写时才算 `match`。
- 每个字段的结果是以下四种之一：
  - `match`：对上了；
  - `mismatch`：原文写的是别的，或者这个字段属于另一个数量、另一列；
  - `not_found`：原文里找不到；
  - `unsupported`：规则证明不了。
- 条目状态：所有字段都是 `match` 才算 `consistent`；只要有 `mismatch` 或 `not_found`，就是 `inconsistent`；其余（含任何 `unsupported`）都是 `unchecked`。`unchecked` 不能当作已检查通过。
- 数字最多支持 18 位有效数字，超出的记 `unsupported`，不做近似。
- 改动任何规则、单位表或转换时，必须同时提升 `checker_version`。

返回：

```json
{"contract_version": "extraction_consistency.v1", "checker_version": "biocheck.extraction_rules.v3",
 "subject": {…},
 "items": [{"id": "m1", "kind": "measurement", "status": "unchecked",
            "fields": {"locator": {"result": "match", "span": {…}},
                       "metric": {"result": "match", "reason": "header_of_value_column", "span": {…}},
                       "measure_kind": {"result": "unsupported", "reason": "measure_kind_not_rule_checkable"}, …}}],
 "counts": {"consistent": 0, "inconsistent": 0, "unchecked": 1}, "llm_calls": []}
```

## 抽取一致性接口 v2（测量，checker v5，T-29 P3）

**POST /v2/extraction-consistency**（契约 `extraction_consistency.v2`，checker `biocheck.extraction_rules.v5`）

- **范围**：只接受 measurement 条目，请求里必须写 `"contract_version": "extraction_consistency.v2"`。/v1（v3）的请求、回执和规则都不变；v1 不会被默认升到 v4。
- **数值**：value 和 denominator 用规范十进制字符串传输（`318`、`21.3`、`-3`），最多 18 位数字、6 位小数，不用 JSON 数字，全程不经过 float。不规范或超出上限的返回 422。
- **period** 是结构化的：`{"type": "point"|"interval", "precision": "day"|"month"|"quarter"|"half"|"year", "value"}`，跨越的区间用 `start`/`end` 代替 `value`。
  - canonical_period.v1 的规范写法逐一列在 `app/extraction_v4.py`；
  - 「截至2023年12月」是时点，「2023年12月」是区间，两者不相等；
  - 不补出原文没有的日；
  - 缺年份或相对期间都是 unsupported；
  - 跨年区间沿用已写出的年份往前推；
  - `past N days (D1 to D2)` 两端都算，天数必须等于 N。
- **新增字段**：
  - `scope_mode`：stated / object_metric_total / unknown；
  - `qualifier`：exact / approximate / at_least / at_most / more_than / less_than；
  - `modality`：actual / planned / projected。
- **回执字段**：每个条目回执 11 个字段结果：locator 加上 object、metric、value、unit、period、scope、denominator、measure_kind、qualifier、modality。定位失败时同样是这 11 个，除 locator 外全部 unsupported。
- **v5（v4 经 r135 修正）在 v3 上新增的规则**（证明不了的一律 unsupported）：
  - **局部句**：值的上下文是它所在的局部句，即 v3 的子句再按英文句末切开；同一段里其他句子的数字、日期和事件词，既不能支持、也不会干扰当前的量。
  - **flow**：period 是区间，同时有事件词。
  - **cumulative**：要有明确的累计语义（累计、cumulative、前N月……）；单纯的 from…to 时间范围、「a total of」或「共」都不算。
  - **stock**：截至时点加状态词。
  - **rate**：沿用 v3；百分比变化（下降6.1%）不算 rate。
  - **scope**：object_metric_total 要求 object 已核且不是指称词、metric 已核、有明确的总量词，并且各字段之外没有限制词。
  - **字段位置**：可以在值之前的同一局部句里（中间没有别的数量），也可以在有效的 markdown 祖先标题里（遇到同级或更高级标题即结束，列出多个名称的标题不算）。
  - **qualifier**：看值前后的标记词。
  - **modality**：看局部句、有效标题和表格标题。
  - **单位**：中文量词、英文计数名词（之前最多两个白名单修饰词）、%、金额，外加 v3 的单位表；空格分隔的千分位不支持。
- **字段 span**：可以重叠，例如 object 落在 metric 之内；passages 仍然不能重叠。
- **测试**：`tests/test_extraction_v4.py` 用 `tests/fixtures/p3_matrix.json`（extraction-contract 样本矩阵 v2：固定 clean_text.v11 产物的真实子句、真实偏移和逐字段原文位置），每一行都走真实 HTTP。`tests/explain_v4.py` 逐行打印状态和非 match 字段的原因，用于审查。

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
