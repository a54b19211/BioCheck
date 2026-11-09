from app import claims as C


def test_split_keeps_offsets_and_gives_trailing_citations_to_their_sentence():
    draft = "2.5 mg/kg组ORR为32%[E1]。中位PFS为2.8个月。[E1] 3.4 mg/kg组ORR为35%[E1][A1]。\n\n第二段没有引用。"
    result = C.sentences(draft)
    assert [s["text"] for s in result] == ["2.5 mg/kg组ORR为32%[E1]。", "中位PFS为2.8个月。[E1]",
                                           "3.4 mg/kg组ORR为35%[E1][A1]。", "第二段没有引用。"]
    assert [s["citations"] for s in result] == [["E1"], ["E1"], ["E1", "A1"], []]
    assert [s["paragraph"] for s in result] == [1, 1, 1, 2]
    assert all(draft[s["start"]:s["end"]] == s["text"] for s in result)


def test_english_decimal_is_not_a_sentence_end_and_comma_lists_are_citations():
    result = C.sentences("ORR was 32% at 2.5 mg/kg [E1, E2]. PFS was 2.8 months [E1].")
    assert [s["citations"] for s in result] == [["E1", "E2"], ["E1"]]


def test_item_numbers_neither_end_a_sentence_nor_count_as_data():
    # r2 first analysis, the "key unknowns" paragraph: "1." and "2." "3." "4." were sentences of their own
    draft = ("关键未知信息包括：1. 2026年10月至11月期间，澳大利亚是否有官方报告的禽流感"
             "（禽类或野生动物）确诊病例、死亡事件或聚集性疫情？"
             "2. 澳大利亚政府机构（如DAFF、州卫生厅）是否发布了针对禽类或野生动物的疫情警报、封锁令或风险评估？"
             "3. 标题中“playing chicken”的具体指代是什么？"
             "是比喻性的长期生态风险讨论，还是指代具体的、即将爆发的急性疫情？"
             "4. 这些GDELT线索的具体来源链接、发布时间及原始语境是什么？")
    result = C.sentences(draft)
    starts = ["关键未知信息包括：1. 2026年", "2. 澳大利亚政府", "3. 标题中“playing", "是比喻性的长期", "4. 这些GDELT"]
    assert len(result) == 5 and all(s["text"].startswith(start) for s, start in zip(result, starts))
    assert all(draft[s["start"]:s["end"]] == s["text"] for s in result)
    assert [C.numeric_tokens(s["text"]) for s in result] == [[]] * 5


def test_a_number_after_an_item_number_is_still_data():
    result = C.sentences("主要发现：1. 肯尼亚报告输入病例9例[E1]。2. 尼日利亚报告1例[E1]。")
    assert [(s["citations"], C.numeric_tokens(s["text"])) for s in result] == [(["E1"], ["9"]), (["E1"], ["1"])]
    assert len(C.sentences("Cases: 31. Deaths: 5.")) == 2  # a final number is not an item number


def test_batches_never_mix_paragraphs_and_hold_at_most_four():
    draft = "一[E1]。二[E1]。三[E1]。四[E1]。五[E1]。\n\n六[E1]。"
    sizes = [[c["index"] for c in b] for b in C.batches(C.sentences(draft))]
    assert sizes == [[1, 2, 3, 4], [5], [6]]


def test_quote_location():
    text = "ORR was 32%. In the other arm ORR was 35%."
    assert C.locate_quote(text, "ORR was 32%") == "found"
    assert C.locate_quote(text, "ORR  was\n32%") == "found"  # whitespace runs count as one
    assert C.locate_quote(text, "ORR was 33%") == "not_found"
    assert C.locate_quote(text, "ORR was") == "ambiguous"
    assert C.locate_quote(text, "") == "not_found"


def test_quote_spans_list_every_place_in_the_texts_own_offsets():
    text = "ORR was 32%.  In the other arm ORR\twas 32%."
    assert [text[s:e] for s, e in C.quote_spans(text, "ORR  was 32%")] == ["ORR was 32%", "ORR\twas 32%"]
    assert C.quote_spans("aaa", "aa") == [(0, 2), (1, 3)]  # places may overlap, as for locate_quote
    assert C.quote_spans(text, " ") == [] and C.quote_spans(text, "ORR was 33%") == []


def test_numeric_hints_list_only_numbers_missing_from_cited_text():
    assert C.numeric_hints("纳入999人，ORR为32%[E1]。", ["ORR was 32% in 223 patients"]) == ["999"]
    assert C.numeric_hints("NCT03525678中ORR为32%[E1]。", ["ORR 32%"]) == []


def test_scope_revision_wraps_only_the_over_reaching_assertion():
    text = "该研究未进行随机化，ORR为32%[E1]。"
    assert C.scope_span_ok(text, "未进行随机化")
    assert C.scope_revision(text, "未进行随机化") == (
        "该研究本次引用材料不足以确认“未进行随机化”这一说法，ORR为32%[E1]。")
    assert not C.scope_span_ok(text, "ORR为32%")          # not a negative assertion
    assert not C.scope_span_ok(text, "ORR为32%[E1]")      # contains a citation
    assert not C.scope_span_ok(text, "未做的事")           # not in the sentence


def test_unverified_source_needs_attribution_and_uncertainty_in_the_same_sentence():
    evidence = {"E2": {"source_kind": "social", "verified": False}}
    bad, _ = C.source_use_problems("某市医院出现多例不明肺炎[E2]。", ["E2"], evidence, [])
    assert bad and bad[0].startswith("unconfirmed_source")
    for ok in ("网帖称某市医院出现多例不明肺炎，尚未证实[E2]。", "待验证的假设是该市出现聚集性肺炎[E2]。",
               "相关帖子随后被删除[E2]。",
               # wordings seen in real drafts: the hedge is there, only phrased differently
               "该帖所述内容尚未证实[E2]。", "社媒“怪病”标题及转发内容未核实，置信低[E2]。",
               "有账号转发同一段消息，发布者身份无法核实[E2]。",
               "关于资金链的匿名说法，尚需银行流水或审计资料等可核查证据[E2]。"):
        assert C.source_use_problems(ok, ["E2"], evidence, [])[0] == []
    only_attributed, _ = C.source_use_problems("网帖称某市医院出现多例不明肺炎[E2]。", ["E2"], evidence, [])
    assert only_attributed


def test_list_markers_are_not_numbers_to_check():
    from app.claims import numeric_tokens
    assert numeric_tokens("就（1）病原而言，(2) 另有12例[E1]") == ["12"]


def test_product_codes_are_not_numbers_to_check():
    from app.claims import numeric_tokens
    assert numeric_tokens("XQ-17与LVR7、COVID-19及XQ301有关，ORR为-0.5") == ["-0.5"]


def test_a_corrected_source_is_flagged_only_until_its_correction_is_read():
    evidence = {"E1": {"source_kind": "official", "verified": True},
                "E4": {"source_kind": "official", "verified": True}}
    updates = [{"target": "E1", "replacement": "E4", "kind": "correction"}]
    # Read: whether a changed part is used as current is the model's call; unchanged content may stand alone.
    assert C.source_use_problems("初稿披露医护患病未证实[E1]。", ["E1"], evidence, updates)[0] == []
    reasons, reads = C.source_use_problems("初稿披露验收20套[E1]。", ["E1"], {"E1": evidence["E1"]}, updates)
    assert reasons[0].startswith("source_updated") and reads[0]["ref"] == "E4"


def test_timestamps_and_glued_source_numbers():
    assert C.numeric_tokens("只用2016-05-03T12:20:01+08:00前的材料，共3例") == ["3"]
    assert C.numeric_hints("95%CI 0.4%–64.1%", ["Clopper-Pearson双侧95%CI0.4%–64.1%"]) == []
    # rounding an analysis output to the digits shown is fine; a different number is not
    assert C.numeric_hints("收入增长28.57%，约28.6%，覆盖比83%", ['{"value": 28.5714}', '{"value": 83.3333}']) == []
    assert C.numeric_hints("收入增长28.58%", ['{"value": 28.5714}']) == ["28.58%"]
    assert C.numeric_hints("共3例", ["共3.4例"]) == ["3"]
    assert C.numeric_tokens("8月30日更正：2019年12月31日起共12例，8月29日07时列14条") == ["12", "14"]
    assert C.numeric_tokens("2019-12-31的帖子称27例，2020/01/04增至44例") == ["27", "44"]
    assert C.numeric_tokens("8月26日至27日门诊5人") == ["5"]
