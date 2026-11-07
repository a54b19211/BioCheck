from app.segment_rules import VisibleSource, find_markers, split_segments, time_clues


def texts(sentence):
    return [s.text for s in split_segments(sentence)]


def test_segments_cut_at_punctuation_and_connectors_and_keep_exact_offsets():
    sentence = "疫情持续，但风险下调并重新评估、监测[E1]。"
    segments = split_segments(sentence)
    assert [s.text for s in segments] == ["疫情持续", "但风险下调", "并重新评估", "监测[E1]。"]
    assert [s.id for s in segments] == ["S1", "S2", "S3", "S4"]
    assert all(sentence[s.start:s.end] == s.text for s in segments)


def test_a_connector_character_cuts_only_where_it_is_a_word_of_its_own():
    # 合并症 / 并发症 / 并未 / 而且 contain the character without joining anything
    assert texts("合并症患者发生并发症，并未住院而且恢复") == ["合并症患者发生并发症", "并未住院而且恢复"]
    assert texts("检测并发现新病例") == ["检测", "并发现新病例"]  # 并 + 发现, not 并发
    assert texts("通报并发布建议") == ["通报", "并发布建议"]
    # English connectors are whole words: brand, android and bandwidth are not cut, and/or and rock-and-roll neither
    assert texts("brand and android but bandwidth") == ["brand", "and android", "but bandwidth"]
    assert texts("and/or rock-and-roll") == ["and/or rock-and-roll"]


def test_nothing_is_cut_inside_brackets_citations_or_a_thousands_group():
    assert len(split_segments("共报告3,462例（确诊3,317例，可能145例）[E1, E2]。")) == 1


def test_markers_are_hints_and_pseudo_triggers_are_not_markers():
    def kinds(text):
        return [(m.kind, m.text) for m in find_markers(text)]
    assert kinds("非洲未报告新病例") == [("negation", "未")]  # 非洲 is Africa
    assert kinds("不同地区不断出现；不过仍在监测") == []
    assert kinds("WHO has not declared a PHEIC, not only in Africa") == [("negation", "not")]
    # the consequent of an if-clause opens with 则, but 规则 / 原则 are nouns
    assert [s.markers[0].text for s in split_segments("如果通报被更正，则结论改变")] == ["如果", "则"]
    assert find_markers("按规则处理") == []


def source(text):
    return VisibleSource(ref="E1", text=text, published_at="2020-05-07", first_seen_at="2026-11-06T03:10:00Z",
                         replay_mode="current")


def test_a_later_date_under_a_stated_version_date_is_only_a_clue():
    body = source("The note of 7 May 2020. As of 19 May 2020, 3462 cases. Discharged on 2020-05-14 and 3 May 2020.")
    clues = time_clues("根据2020年5月7日发布的WHO通报[E1]，风险为中等。", [body])
    assert [(c["code"], c["ref"], c["stated"], c["later_dates"]) for c in clues] == [
        ("later_than_stated_date", "E1", "2020-05-07", ["2020-05-14", "2020-05-19"])]
    assert clues[0]["first_seen_at"] == "2026-11-06T03:10:00Z" and clues[0]["replay_mode"] == "current"
    # the version on file is named, the date is not tied to a document, or the text holds no later date: no clue
    assert time_clues("根据2020年5月7日发布的WHO通报（现存版本）[E1]，风险为中等。", [body]) == []
    assert time_clues("截至2020年5月19日，报告3462例[E1]。", [body]) == []
    assert time_clues("根据2020年5月7日发布的WHO通报[E1]。", [source("Published 7 May 2020.")]) == []
