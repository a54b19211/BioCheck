# BioCheck

维护人：潘倩。

BioCheck 是 BioRadar 的一个专业服务：根据雷达给的固定材料写一份中文研究回答，然后**逐句**核对这份稿子是否被它引用的原文支持，最后只对明确有错的句子做一次有限修订。材料检索、版本、预算和报告保存都由雷达负责；BioCheck 只看本次请求里给的材料，不联网补事实。

## 运行服务

CPython 3.12，依赖只从锁文件装（见下文「开发环境与测试」）。

```sh
make venv                                # .venv，只按 requirements.lock 安装，每个包都校验 sha256
. .venv/bin/activate
export LLM_PROVIDER=deepseek             # deepseek 或 openai（OpenAI 兼容接口）
export LLM_BASE_URL=https://api.deepseek.com
export LLM_API_KEY=sk-...
export LLM_MODEL=deepseek-chat
export LLM_TIMEOUT_SECONDS=180           # 可选，默认 180
export LLM_WRITE_THINKING=false          # 可选，只影响 generate；核验与修订总是关闭思考
export SERVICE_TOKEN=一个足够长的随机串
uvicorn app.service:app --host 127.0.0.1 --port 18562
```

契约包钉在 2.0.0（只描述 checker v3 和 v17；v17 / 契约 v3 用 normalize 的 `unit_subject_head_v4`、`period_span_whole_v5` 和 `decimal_text_v6` 读数）。`make check-install` 在一个全新的虚拟环境里按锁文件安装，再逐个导入 `app/` 下的模块，并在这个环境里跑一遍测试（规则运行时才会读契约包的函数），钉的版本缺东西时在这里就失败（`UV_OFFLINE=1` 只用本地 uv 缓存）。

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
- `evidence[].window`（可选）：说明 `text` 是这份材料的哪一部分，由送材料的一方按它实际截取的范围给出：`{"coverage": "full|excerpt|unknown", "chars": text 字数, "record_chars": 全文字数, "ranges": [[起, 止], ...], "ranges_omitted": 没列出的段数}`，`ranges` 是 `text` 取自全文的字符位置（左闭右开），之间的内容没有给本服务。不带 `window`（旧调用方）按 `unknown` 处理，不当作全文；`unknown` 和 `excerpt` 一样按片段对待：片段里没有 X，只能写成“本次片段未提供X，尚未查清”，不能判成全文或本次检索也没有。`/v2/verify` 的 `window` 同样拒绝未知字段。
- `max_llm_calls`（默认 20）：本次最多调用几次模型，用完后剩下的句子判为待核（`unchecked`）。

**POST /v1/generate** → `{"draft": "……", "llm_calls": [...]}`

请求可带 `slots`：`[{"slot": "J1", "question": "……"}]`，雷达列出的、稿件必须回答的问题（编号由雷达给）。带了 `slots` 时稿件按段写，响应多一个 `sections`，`draft` 是各段用空行连起来的结果（每段内的空白合并为一个空格，所以一段就是核验时的一个段落）：

```json
{"draft": "……\n\n……", "llm_calls": [...],
 "sections": [{"kind": "judgment", "slot": "J1", "status": "supported", "text": "……[E1]。"},
              {"kind": "evidence", "text": "……[E1]。"}]}
```

`kind` 取 `judgment|evidence|gaps|scenarios`（没有任务方目标，不写 `recommendation`）；只有 `judgment` 段带 `slot`（请求给的槽，或假设编号如 `H1`）和 `status`（`supported|refuted|insufficient|open|conflict`；假设段不带 `status`）。服务不核对 `slot` 是否是请求给过的编号，放不进去的段落由雷达丢弃并记录。不带 `slots` 的请求，提示词、输出和响应与以前完全一致。

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
- `review` 必须是对同一份 `draft` 的核验结果，否则返回 409 `review_mismatch`。可以是 `/v1/verify` 或 `/v2/verify` 的结果：`/v2/verify` 的句子没有 `quotes`，短引取自各单元的 `quotes`（找到一处或多处都算找到）。
- 改完后由雷达再调一次核验。

错误统一为 `{"error_code": "...", "message": "..."}`：401 `unauthorized`、400 `invalid_request`/`no_materials`/`duplicate_id`、409 `review_mismatch`、502 生成失败（附 `llm_calls`）。

## 按断言单元核验（/v2/verify）

**POST /v2/verify**：请求是 `/v1/verify` 的请求（含 `draft`），但**拒绝任何未知字段**（包括嵌套对象里的），`evidence` 每条可多带 `first_seen_at`、`proof_available_at`、`replay_mode`（`current|operational_replay|public_history_reconstruction`，发布日期仍是 `published_at`），另有可选的 `rules_version`：调用方期望的句段规则版本，与服务的不同返回 409 `rules_version_mismatch`。还可带 `sections`：`[{"kind": "judgment", "start": 0, "end": 120}]`，稿件每个段落所属的报告节，偏移是 `draft` 的字符位置（左闭右开，同句子的 `start/end`）；`kind` 取 `question|judgment|evidence|gaps|scenarios|uncertainty|visibility|revision|recommendation`，各节不能重叠、不能越出 `draft`，否则 400。完全落在 `judgment`、`gaps`、`scenarios`、`recommendation` 之外的节里的句子是“事实节”的句子：其中的推断单元（`kind=inference`）模型判 `supported` 时降为 `insufficient`（`inference_in_fact_section`）。没带 `sections`，或句子不在任何一节里，与以前一样。节不送给模型。

响应在 `/v1/verify` 的 `status`、`claims`、`coverage`、`read_requests`、`llm_calls` 之外多：顶层 `checker_version`、`rules_version`；每个句子多 `segments`、`units`、`form_check`、`semantic_split`、`call`（`claims[].labels` 是各单元标签的并集）。`call` 是最后一次载着该句的 `verify_claims` 调用在响应 `llm_calls` 里的下标；该句没有送模型（引用了不存在的编号、`batch_limit`、已达 `max_llm_calls`）时为 `null`。批被截断后拆开重来的，指拆开后的那一次。

- `segments[]`：程序按标点和连接词切的候选句段 `{id: "S1", start, end, text, markers, status, units}`；`status` 为 `handled`（有单元处理）、`unhandled`、`unchecked`（句子没有送模型或模型没答）。
- `units[]`：程序编号 `U1..`（作废的单元也占号）、`segment`、`fragment`、`start/end`（句内偏移）、`kind`（`fact|negated_fact|condition|inference|time|no_claim`）、`model_verdict`（模型原判）、`verdict`（规则之后）、`reason`、`void_reason`（片段定位不到或不唯一时的作废理由）、`labels`、`governed_by`（程序映射后的单元编号，或请求预先给的编号如假设 `H1`）、`conditional`（条件，或受条件管辖）、`field_basis`（否定依据为结构字段时的路径、值和是否对得上）、`quotes[]`（`{ref, quote, status, positions, adopted}`：短引在原文中的全部位置，和被采用的那一处；重复出现而模型没指明哪一处时不采用，不默认取第一处）。
- `form_check`：程序能确定的部分（每个句段有单元或标为非事实、无空结果、重叠已声明、`governed_by` 能映射、句段里的否定词没有在单元里丢掉）。`passed` 不代表拆分完整。
- `semantic_split`：模型自评单元是否涵盖全句（`complete|incomplete|uncertain`，没报是 `not_reported`），标 `model_self_assessment`。不是 `complete` 时句子不能判 `supported`，也不能判 `no_claim`（都降为 `insufficient`）。
- 句子判定由程序汇总：任一单元 `contradicted` 则 `contradicted`；形式检查失败或有单元 `insufficient` 则 `insufficient`；条件单元不算已陈述的事实。

批次：每批最多 4 句、24 个句段、同一段；超过 8 个句段或 300 字的句子单独成批；超过 24 个句段的句子不送模型，记 `batch_limit`。模型输出被截断时，这一批拆成两半重来，直到单句；单句仍被截断记 `output_truncated`。拆批和重试计入 `max_llm_calls`。

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

## 测量规则（`app/measurement_rules.py`）

测量条目的规则在 `app/measurement_rules.py`，由下面的 `/v3/extraction-consistency` 调用。原来的 `POST /v2/extraction-consistency`（契约 `extraction_consistency.v2`，checker `biocheck.extraction_rules.v10`）已经删除：Fabric 和 Radar 都不调用它。

- **范围**：只有 measurement 条目。/v1 的请求、回执和规则不变。
- **数值**：value 和 denominator 用规范十进制字符串传输（`318`、`21.3`、`-3`），最多 18 位数字、6 位小数，不用 JSON 数字，全程不经过 float。不规范或超出上限的返回 422。
- **period** 是结构化的：`{"type": "point"|"interval", "precision": "day"|"month"|"quarter"|"half"|"year", "value"}`，跨越的区间用 `start`/`end` 代替 `value`。
  - canonical_period.v1 的规范写法逐一列在 `app/measurement_rules.py`；
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
- **在 v1 规则（`app/item_rules.py`）之上新增的规则**（证明不了的一律 unsupported）：
  - **局部句**：值的上下文是它所在的局部句，即 v1 的子句再按英文句末切开；同一段里其他句子的数字、日期和事件词，既不能支持、也不会干扰当前的量。
  - **flow**：period 是区间，同时有事件词。
  - **cumulative**：要有明确的累计语义（累计、cumulative、前N月……）；单纯的 from…to 时间范围、「a total of」或「共」都不算。
  - **stock**：截至时点加状态词。
  - **rate**：沿用 v1；百分比变化（下降6.1%）不算 rate。
  - **scope**：object_metric_total 要求 object 已核且不是指称词、metric 已核、有明确的总量词，并且各字段之外没有限制词。
  - **字段位置**：可以在值之前的同一局部句里（中间没有别的数量），也可以在有效的 markdown 祖先标题里（遇到同级或更高级标题即结束，列出多个名称的标题不算）。
  - **qualifier**：看值前后的标记词。
  - **modality**：看局部句、有效标题和表格标题。
  - **单位**：中文量词、英文计数名词（之前最多两个白名单修饰词）、%、金额，外加 v1 的单位表；数值后没有单位时，也可以是绑定在 metric 短语主语上的计数名词（`14 close contacts`），但包含/排除/举例之后点名的子集不算单位。
  - **数字和期间的读法**用共享契约包的 `extraction_contract.normalize`（与 Radar 同一份）：千分位可以用逗号、空格、不换行空格分组；英文单独年份（`in 2018`）是年度区间；period 的 span 必须是完整写出的期间，后面还接着月、日、区间或起点的不算（`In January 2018` 里的 `2018` 不是期间）。
- **数量短语完整**：值所在组里，数量自己的短语必须被本条目的字段覆盖。英文从值到第一个助动词；中文是整组，只允许列出的虚词留在字段外。介词和冠词可以连接字段；事件动词只有在 metric 之后（作谓语）才不需要字段。已核字段所对应的标记词（声明 cumulative 时的「累计」、声明 planned 时的「计划」、声明非 exact 时的比较词）算作已覆盖。漏掉的限定语（suspected、in humans、批准注册、表格行的类别）会让 metric 给 unsupported。
- **比较短语**：比较词按整个短语、从长到短匹配，包括否定形式（不超过是上限）；清单里没有的否定写法给 unsupported。值前后的限定和「无单位」都在有边界的局部句里判断，会跳过空白。百分数只能用紧贴它写出的比例。
- **谓语否定**：值所在组里，如果字段之外出现否定词（not、never、without、n't、未、没有、尚未、无、不），measure_kind 给 unsupported（negated_predicate）。否定句里的事件词不算肯定的事件依据。字段内部的否定（如 metric 里的「不合格」、已声明的「不超过」）属于该字段本身，不受影响。
- **字段 span**：可以重叠，例如 object 落在 metric 之内；passages 仍然不能重叠。
- **测试**：`tests/test_measurement_rules.py` 用 `tests/fixtures/p3_matrix.json`（extraction-contract 样本矩阵 v2：固定 clean_text.v11 产物的真实子句、真实偏移和逐字段原文位置），每一行都走真实 HTTP。`tests/explain_measurement_rules.py` 逐行打印状态和非 match 字段的原因，用于审查。

## 抽取一致性接口 v3（测量加业务维度）

**POST /v3/extraction-consistency**（契约 `extraction_consistency.v3`，checker `biocheck.extraction_rules.v17`，规则在 `app/dimension_rules.py`）

- 请求里的测量条目如上一节，另外每个测量条目可以带 `dims`（每类至多一个：disease、population、date_basis、indication、product_or_class、milestone_definition、entity、stage、denominator_definition，各自写明 value 和 span）和 `family`。
- 测量字段和条目状态用上一节的规则，读法按这一版契约：metric 短语里的单位按 `extraction_normalize.v4`，完整期间按 v5（上一行以完整日期结尾，例如公告的「发布时间」，不算期间的延续）；产出类单位（`120万支` 的单位是 `万支`，数量级不丢）和「实际产出」才算 flow。
- **英文写法**（`docs/decisions/0008-english-wording-checker-v17.md`；只对不含中日韩字符的局部句生效）：
  - 一句里有多个数时每个数各自核验：值之后的字段，只要它和值之间没有别的数就是这个值的；值之前的字段，前面没有别的数才是；括号里的百分数（`(CFR of 14%)`）不是另一个数；括号里的逗号不切开值所在的组。
  - 一句列出多个数（`N cases, including M deaths`、`N cases and M deaths`）且句中只有一个期间时，期间属于列表里的每个数：条件是它在所有数之前或之后，句中没有别的日期，每个数（第一个除外）由 including / and / of which 等引出。
  - `a total of` 是总量词；`As of <date>, a total of N` 证明累计口径；只写起点的期间（`since September 2012`）是累计，不是 flow。
  - 期间的新写法：混合精度（`Between August 2015 and 23 January 2016` 是日精度，首月从 1 日起）、ISO 周（`week 43 of 2017`、`week 34 (ending 27 August 2023)`，周一到周日）、`On 12 November 2015`（一天的区间）、年区间（`Between 2017 and 2021` 是月精度 2017-01 到 2021-12）、只有 `start` 的开放区间。没写年份、`to date`、`So far` 照旧不支持。
  - 文本里有结构依据的错读判值 mismatch（条目 inconsistent）：数字粘在词后（`case1`）、写出的区间的一端（`between 38 to 74`）、更正声明里的旧值（`corrected from 1,595`）、`N of the M confirmed cases died` 里的 N 当成病例数。紧跟指标末词的一位数字（`linked 2`）只凭位置证明不了是脚注（后置的计数也可能这样写），值给 unsupported（`value_may_be_a_footnote_mark`），条目 unchecked。只是有歧义的仍是 unchecked。
  - 拼写的 one 到 twelve 可以作为值（`extraction_normalize.v6`）。
- 每个维度另给一个结果 `dims[kind]`，只有同时满足以下几条才是 match：原文在该 span 处写着这个值；文本里有这类角色的明确依据（标签、病例名词、人群词、「按报告日统计」这类口径写法等，机构、日期和裸数字都不算）；它所在短语没有否定、排除，也没有和别的成员并列；它和值在同一局部句且中间没有别的数量，或者在值的有效祖先标题里；它填满了一个能识别的结构位置。其余一律 unsupported。维度结果不改变条目状态。

## 开发环境与测试

唯一的入口是 `Makefile`。需要 uv 0.13.0（在 PATH 上，例如 `~/.local/bin/uv`；或者 `make UV=<路径>`），以及旁边 extraction-contract 检出里构建好的 wheel（`../extraction-contract/dist`，或 `EXTRACTION_CONTRACT_WHEELS=<目录>`）。

```sh
make venv            # .venv：只按 requirements.lock 安装（含契约 wheel），每个包都校验 sha256
make test            # 全部测试（不联网，模型用 FakeLLM）
make lint            # ruff
make metrics         # 函数长度 80 行 / 圈复杂度 20 的门槛（scripts/code_metrics.py；例外清单 scripts/metrics_exceptions.txt 只许变短）
make check-install   # 一个用完即删的新环境：按锁文件安装、逐个导入 app/ 模块、再跑一遍测试
make check-lock      # requirements.lock 是否仍与 requirements*.txt 一致（要访问包索引）
```

`make lint` 的规则：所有仓库共用一份基础规则 `E4, E7, E9, F, W, I`（devtools `README.md` 的“共用的代码门槛”）。圈复杂度只由 `make metrics` 管，不开 ruff 的 C90。在基础规则之上多开规则的仓库：

| 仓库 | 多开的规则 |
|---|---|
| BioRadar | `E`（含行宽 E501，110） |
| BioCheck | `E`（含行宽 E501，120） |

`make check-lock`、`make lock` 要访问 PyPI（`make venv`、`make check-install` 在 uv 缓存里没有的包也要下载）。zktest 上直连 PyPI 有时超时，这时只给这几条命令临时加上本机已有的 HTTP 代理（mihomo，监听 127.0.0.1:7890），例如 `HTTPS_PROXY=http://127.0.0.1:7890 HTTP_PROXY=http://127.0.0.1:7890 make check-lock`；不要把代理写进全局配置。`check-lock` 的结果有两种失败：解析本身失败（包索引或网络不可用、依赖解不出来）时报 `check-lock did not run`，make 显示 `Error 2`，锁文件没有比较过，不能因此重做锁；只有解析成功而内容不同时才报 `requirements.lock is stale`（make 显示 `Error 1`）。两种情况 make 都以非零状态结束。

- 直接依赖写在 `requirements.txt`（运行 + 测试 + ruff）、`requirements-contract.txt`（契约 wheel 的版本和 sha256）和 `requirements-transport.txt`（模型调用的 HTTP 一侧，共享包 `zk-llm-transport` 0.1.1 的 wheel 哈希；wheel 在同级 `llm-transport/dist`，或 `LLM_TRANSPORT_WHEELS=<目录>`）；`requirements.lock` 是由它们解析出的完整锁，每个包都带 sha256，解析目标是 CPython 3.12、x86_64-unknown-linux-gnu（锁文件头一行写着解析命令）。改了这些文件后运行 `make lock`，再提交锁文件。
- 已验证的组合：uv 0.13.0、CPython 3.12.15；通过数量以 `make check` 的输出为准，这里不写数字。

`tests/test_checker_golden.py` 用 `tests/fixtures/checker_golden.jsonl` 逐字节比对三个契约的全部样例回执；只有提升 `CHECKER_VERSION` 时才用 `PYTHONPATH=. python tests/golden.py` 重新生成。

测试不联网，用 `tests/conftest.py` 里的 `FakeLLM` 代替模型。`evaluation/` 下保留 `tasks.json`（题目及 Fabric 记录版本）、`controls.json` 和 `regressions.json`（正确/错误对照句），供以后用真实材料评测。

## 设计决定

`docs/decisions/` 下记录了仍有用的历史：改成同步 HTTP 服务的原因（0002）、抽取 checker 的版本规则和每个版本改了什么（0001）。

## 已知限制

- 短引只做逐字查找（空白合并），模型稍有改写就会判 `quote_not_found`，结果偏保守（多出 REVIEW）。
- 社媒归因、历史回顾、范围修订的措辞检查靠关键词，可能漏判少见说法；实质语义仍由模型判断。
- 分析结果按整段 JSON 文本查短引，不再按 output 路径定位；同一数字出现在不同字段时由模型和短引唯一性共同把关。
- 每批都会重发所引原文全文，长材料较费 token。
- 覆盖检查只看到材料标题和逐句结论，看不到原文全文，可能漏报“遗漏了某个重要结果”。
- 还没有用真实模型跑过新版本的评测；生成/核验共用同一模型，不能代替独立的人工专业标注。
