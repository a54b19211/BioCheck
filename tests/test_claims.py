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


def test_numeric_hints_list_only_numbers_missing_from_cited_text():
    assert C.numeric_hints("纳入999人，ORR为32%[E1]。", ["ORR was 32% in 223 patients"]) == ["999"]
    assert C.numeric_hints("NCT03525678中ORR为32%[E1]。", ["ORR 32%"]) == []


def test_scope_revision_wraps_only_the_over_reaching_assertion():
    text = "该研究未进行随机化，ORR为32%[E1]。"
    assert C.scope_span_ok(text, "未进行随机化")
    assert C.scope_revision(text, "未进行随机化") == "该研究本次引用材料不足以确认“未进行随机化”这一说法，ORR为32%[E1]。"
    assert not C.scope_span_ok(text, "ORR为32%")          # not a negative assertion
    assert not C.scope_span_ok(text, "ORR为32%[E1]")      # contains a citation
    assert not C.scope_span_ok(text, "未做的事")           # not in the sentence


def test_unverified_source_needs_attribution_and_uncertainty_in_the_same_sentence():
    evidence = {"E2": {"source_kind": "social", "verified": False}}
    bad, _ = C.source_use_problems("某市医院出现多例不明肺炎[E2]。", ["E2"], evidence, [])
    assert bad and bad[0].startswith("unconfirmed_source")
    for ok in ("网帖称某市医院出现多例不明肺炎，尚未证实[E2]。", "待验证的假设是该市出现聚集性肺炎[E2]。",
               "相关帖子随后被删除[E2]。"):
        assert C.source_use_problems(ok, ["E2"], evidence, [])[0] == []
    only_attributed, _ = C.source_use_problems("网帖称某市医院出现多例不明肺炎[E2]。", ["E2"], evidence, [])
    assert only_attributed


def test_list_markers_are_not_numbers_to_check():
    from app.claims import numeric_tokens
    assert numeric_tokens("就（1）病原而言，(2) 另有12例[E1]") == ["12"]
