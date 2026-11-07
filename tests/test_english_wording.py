"""Contract v3, English wording (docs/decisions/0008): the sentences are those of the 24 sealed windows of the D1
measurement batch (WHO Disease Outbreak News; the annotation is AI-assisted and not reviewed by a person). Per
item of the ruling one or two readings that are right and ones that are wrong, each with the text of its notice."""

import pytest
from test_dimension_rules import answer
from test_measurement_rules import at, client, fields_for, synthetic  # noqa: F401

from app import measurement_rules


def day(start, end):
    return {"type": "interval", "precision": "day", "start": start, "end": end}


def ask(client, text, quote, fields):  # noqa: F811
    body = synthetic(text, quote, fields)
    body["contract_version"] = "extraction_consistency.v3"
    return answer(client, body)


def seen(item, *names):
    return {n: (item["fields"][n]["result"], item["fields"][n].get("reason")) for n in names}


def span_of(f, name, text, piece, shift, size):
    """Put the span of `name` at text.index(piece) + shift (the first piece is not always the one meant)."""
    i = 1000 + text.index(piece) + shift
    f[name]["span"] = {"passage": "p1", "start": i, "end": i + size}


# ---- the period forms ---------------------------------------------------------------------------------------------

@pytest.mark.parametrize("text,expected", [
    ("Between August 2015 and 23 January 2016", day("2015-08-01", "2016-01-23")),
    ("Between January to 28 September 2022", day("2022-01-01", "2022-09-28")),
    ("During week 43 of 2017", day("2017-10-23", "2017-10-29")),
    ("In week 34 (ending 27 August 2023)", day("2023-08-21", "2023-08-27")),
    ("since September 2012", {"type": "interval", "precision": "month", "start": "2012-09"}),
    ("On 12 November 2015", {"type": "interval", "precision": "day", "value": "2015-11-12"}),
    ("Between 2017 and 2021", {"type": "interval", "precision": "month", "start": "2017-01", "end": "2021-12"}),
])
def test_the_english_period_forms_of_the_batch(text, expected):
    assert measurement_rules.canonical_period(text, english=True) == (expected, None)


@pytest.mark.parametrize("text,reason", [
    ("As of 6 September", "period_year_not_stated"), ("Between 21 January and 16 February", "period_year_not_stated"),
    ("to date", "period_form_not_supported"), ("So far", "period_form_not_supported"),
    ("During week 54 of 2017", "invalid_date"), ("Between 2021 and 2017", "range_ends_before_it_starts"),
])
def test_a_period_without_a_year_or_a_date_stays_refused(text, reason):
    assert measurement_rules.canonical_period(text, english=True) == (None, reason)


def test_the_forms_belong_to_the_english_switch_only():
    for text in ("On 12 November 2015", "Between 2017 and 2021", "since September 2012", "During week 43 of 2017"):
        assert measurement_rules.canonical_period(text)[0] is None
    assert measurement_rules.canonical_period("Between August 2015 and 23 January 2016") == (
        None, "mixed_precision_range")


# ---- several numbers in one sentence: each with the period, the metric and the object it has ----------------------

W17 = ("# Dengue - Nepal\n\nBetween January to 28 September 2022, a total of 28 109 confirmed and suspected dengue "
       "cases and 38 confirmed deaths due to dengue have been recorded, affecting all seven provinces.\n")


def test_the_second_number_of_a_list_has_the_period_of_the_sentence_and_a_total_of(client):  # noqa: F811
    f = fields_for(W17, "38", "deaths", "Between January to 28 September 2022", day("2022-01-01", "2022-09-28"),
                   obj="dengue", metric="confirmed deaths due to dengue", mode="object_metric_total")
    f["object"]["span"] = at(W17, "dengue", 1)
    item = ask(client, W17, "38 confirmed deaths due to dengue have been recorded", f)
    assert item["status"] == "consistent", seen(item, *item["fields"])
    assert seen(item, "period", "scope") == {"period": ("match", "period_list"),
                                             "scope": ("match", "object_metric_total")}


W09 = ("# Meningococcal meningitis – Niger\n\nBetween 1 January and 28 June 2015, the Ministry of Public Health of "
       "Niger notified WHO of 8,500 suspected cases of meningococcal meningitis, including 573 deaths.\n")
W09_PERIOD = day("2015-01-01", "2015-06-28")


def test_a_number_after_including_has_the_period_before_the_first_number(client):  # noqa: F811
    f = fields_for(W09, "573", "deaths", "Between 1 January and 28 June 2015", W09_PERIOD,
                   obj="meningococcal meningitis", metric="deaths")
    item = ask(client, W09, "including 573 deaths", f)
    assert seen(item, "period", "object", "metric", "qualifier") == {
        "period": ("match", "period_list"), "object": ("match", "object_before"),
        "metric": ("match", "metric_group"), "qualifier": ("match", "exact_by_position")}


W10 = ("# Dengue – Burkina Faso\n\nAs of 3 November 2017, a total of 9 029 (suspected, probable or confirmed) cases "
       "and 18 deaths (CFR= 0.2%) were reported throughout the country.\n")


def test_a_noun_phrase_after_another_number_is_not_this_numbers_metric(client):  # noqa: F811
    f = fields_for(W10, "9 029", None, None, None, kind="cumulative", metric="deaths")
    f["value"]["value"] = "9029"
    item = ask(client, W10, "a total of 9 029 (suspected, probable or confirmed) cases and 18 deaths", f)
    assert seen(item, "metric")["metric"] == ("unsupported", "several_quantities_in_value_sentence")


# ---- a total of, and the total to a date -------------------------------------------------------------------------

def test_as_of_a_date_a_total_of_is_a_cumulative_count_and_a_rate_in_brackets_is_no_quantity(client):  # noqa: F811
    f = fields_for(W10, "18", "deaths", "As of 3 November 2017", {"type": "point", "precision": "day",
                                                                  "value": "2017-11-03"},
                   kind="cumulative", obj="Dengue", metric="deaths", scope="throughout the country")
    item = ask(client, W10, "a total of 9 029 (suspected, probable or confirmed) cases and 18 deaths", f)
    assert item["status"] == "consistent", seen(item, *item["fields"])
    assert seen(item, "period", "measure_kind") == {"period": ("match", "period_list"),
                                                    "measure_kind": ("match", "as_of_total")}


W20 = ("# Cholera- Haiti\n\nNationally, a total of 820 000 cases of cholera including 9792 deaths were reported "
       "between October 2010 and February 2019.\n")


def test_a_total_of_is_a_total_word_and_the_period_after_the_list_is_the_lists(client):  # noqa: F811
    f = fields_for(W20, "820 000", "cases", "between October 2010 and February 2019",
                   {"type": "interval", "precision": "month", "start": "2010-10", "end": "2019-02"},
                   obj="cholera", metric="cases of cholera", mode="object_metric_total")
    f["value"]["value"] = "820000"
    item = ask(client, W20, "a total of 820 000 cases of cholera including 9792 deaths were reported", f)
    assert item["status"] == "consistent", seen(item, *item["fields"])
    assert seen(item, "period", "scope") == {"period": ("match", "period_list"),
                                             "scope": ("match", "object_metric_total")}


W24_YEARS = ("Six of the nine cases occurred in 2022 (July, October and December, n=2 each) and three cases in 2023 "
             "(January (n=1) and April (n=2)).")


def test_a_sentence_with_two_periods_shares_neither(client):  # noqa: F811
    f = fields_for(W24_YEARS, "1", None, "in 2022", {"type": "interval", "precision": "year", "value": "2022"},
                   kind="cumulative", metric="n=1")
    item = ask(client, W24_YEARS, "three cases in 2023 (January (n=1)", f)
    assert item["fields"]["period"]["result"] == "unsupported"


# ---- the period forms, end to end --------------------------------------------------------------------------------

W14 = ("# Middle East respiratory syndrome coronavirus – Republic of Korea\n\nGlobally, since September 2012, WHO has "
       "been notified of 1,599 laboratory-confirmed cases of infection with MERS-CoV, including at least 574 related "
       "deaths.*\n")


def test_since_a_month_is_a_period_open_at_its_end_and_a_cumulative_count(client):  # noqa: F811
    period = {"type": "interval", "precision": "month", "start": "2012-09"}
    f = fields_for(W14, "1,599", "cases", "since September 2012", period, kind="cumulative", obj="MERS-CoV",
                   metric="laboratory-confirmed cases of infection with MERS-CoV", scope="Globally")
    f["value"]["value"] = "1599"
    quote = "WHO has been notified of 1,599 laboratory-confirmed cases of infection with MERS-CoV"
    item = ask(client, W14, quote, f)
    assert item["status"] == "consistent", seen(item, *item["fields"])
    assert seen(item, "measure_kind")["measure_kind"] == ("match", "cumulative_since_start")
    flow = ask(client, W14, quote, dict(f, measure_kind="flow"))
    assert seen(flow, "measure_kind")["measure_kind"] == ("unsupported", "open_period_not_a_flow")


W18 = ("# Diphtheria-Nigeria\n\nIn week 34 (ending 27 August 2023), 234 suspected cases have been reported from 20 "
       "LGAs in five states, with one Lab confirmed case1 from the 22 samples collected. Eighteen of these cases "
       "were epidemiologically linked 2 and 141 were classified as clinically compatible.\n")


def test_a_week_ending_on_a_date_is_seven_days(client):  # noqa: F811
    f = fields_for(W18, "234", "cases", "In week 34 (ending 27 August 2023)", day("2023-08-21", "2023-08-27"),
                   obj="Diphtheria", metric="suspected cases", scope="20 LGAs in five states")
    item = ask(client, W18, "234 suspected cases have been reported from 20 LGAs in five states", f)
    assert item["status"] == "consistent", seen(item, *item["fields"])


W01 = ("# Zika virus disease – Suriname\n\nOn 12 November 2015, the National IHR Focal Point of Suriname notified "
       "PAHO/WHO of 4 additional cases of Zika virus infection.\n")


def test_a_single_reporting_day_is_an_interval_of_one_day(client):  # noqa: F811
    f = fields_for(W01, "4", "cases", "On 12 November 2015", {"type": "interval", "precision": "day",
                                                              "value": "2015-11-12"},
                   obj="Zika virus infection", metric="additional cases", scope="Suriname")
    f["scope"]["span"] = at(W01, "Suriname", 1)
    item = ask(client, W01, "notified PAHO/WHO of 4 additional cases of Zika virus infection", f)
    assert item["status"] == "consistent", seen(item, *item["fields"])


# ---- explicit errors are inconsistent, ambiguous readings stay unchecked -----------------------------------------

def test_a_digit_glued_to_a_word_is_no_number(client):  # noqa: F811
    f = fields_for(W18, "1", None, None, None, metric="Lab confirmed case")
    item = ask(client, W18, "with one Lab confirmed case1 from the 22 samples", f)
    assert item["status"] == "inconsistent"
    assert seen(item, "value")["value"] == ("mismatch", "value_attached_to_a_word")


def test_a_digit_behind_the_metrics_last_word_is_a_footnote_mark(client):  # noqa: F811
    f = fields_for(W18, "2", None, None, None, metric="epidemiologically linked")
    span_of(f, "value", W18, "linked 2", 7, 1)
    item = ask(client, W18, "epidemiologically linked 2 and 141 were classified", f)
    assert item["status"] == "inconsistent"
    assert seen(item, "value")["value"] == ("mismatch", "value_is_a_footnote_mark")


W11 = ("# Zika virus disease–Panama\n\nOn 14 December 2015, the National IHR Focal Point (NFP) of Panama notified "
       "PAHO/WHO of one (1) additional laboratory-confirmed case of Zika virus infection.\n")


def test_a_number_in_brackets_after_its_word_form_is_a_count(client):  # noqa: F811
    f = fields_for(W11, "1", None, None, None, metric="additional laboratory-confirmed case")
    span_of(f, "value", W11, "(1)", 1, 1)
    item = ask(client, W11, "notified PAHO/WHO of one (1) additional laboratory-confirmed case", f)
    assert seen(item, "value")["value"] == ("match", None)


W23 = ("# Legionellosis - Poland\n\nBetween 2017 and 2021, Poland reported between 38 to 74 cases of Legionnaires’ "
       "disease annually to the European Surveillance System (TESSy).\n")


@pytest.mark.parametrize("end", ["38", "74"])
def test_one_end_of_a_written_range_is_not_the_count(client, end):  # noqa: F811
    f = fields_for(W23, end, None, None, None, metric="cases of Legionnaires’ disease")
    item = ask(client, W23, "Poland reported between 38 to 74 cases of Legionnaires’ disease annually", f)
    assert item["status"] == "inconsistent"
    assert seen(item, "value")["value"] == ("mismatch", "value_is_one_end_of_a_range")


W15 = ("# Middle East respiratory syndrome coronavirus – Republic of Korea\n\nThe global case and death counts were "
       "corrected from 1,595 and 571, respectively.\n")


@pytest.mark.parametrize("old", ["1,595", "571"])
def test_the_old_values_of_a_correction_are_not_current(client, old):  # noqa: F811
    f = fields_for(W15, old, None, None, None, metric="global case and death counts")
    f["value"]["value"] = old.replace(",", "")
    item = ask(client, W15, "The global case and death counts were corrected from 1,595 and 571", f)
    assert item["status"] == "inconsistent"
    assert seen(item, "value")["value"] == ("mismatch", "value_is_the_superseded_one")


W16 = ("# Marburg virus disease - Equatorial Guinea\n\nA total of 17 confirmed and 23 probable cases were reported "
       "from five districts in four provinces; 12 of the 17 confirmed cases died and all of the probable cases were "
       "reported deaths.\n")


def test_n_of_the_m_cases_who_died_is_not_a_count_of_the_cases(client):  # noqa: F811
    wrong = fields_for(W16, "12", None, None, None, metric="confirmed cases")
    item = ask(client, W16, "12 of the 17 confirmed cases died", wrong)
    assert item["status"] == "inconsistent"
    assert seen(item, "value")["value"] == ("mismatch", "value_is_part_of_the_metric_population")
    died = fields_for(W16, "12", None, None, None, metric="died")
    assert seen(ask(client, W16, "12 of the 17 confirmed cases died", died), "value")["value"] == ("match", None)


# ---- spelled counts (extraction_normalize.v6) --------------------------------------------------------------------

W24 = ("# Enterovirus-Echovirus 11 Infection -  the European Region\n\nAs of 26 June 2023, France has reported nine "
       "cases of severe E-11 neonatal sepsis associated with hepatic impairment and multi-organ failure.\n")


def test_a_count_written_as_a_word_is_a_whole_number(client):  # noqa: F811
    f = fields_for(W24, "nine", "cases", None, None, kind="cumulative", metric="cases")
    f["value"]["value"] = "9"
    item = ask(client, W24, "France has reported nine cases of severe E-11 neonatal sepsis", f)
    assert seen(item, "value", "unit") == {"value": ("match", None), "unit": ("match", "unit_at_value")}


W05 = ("# Cholera – United Republic of Tanzania\n\nBy late July 2015, the Kigoma region also became affected – "
       "although no new case has been recorded in this area in more than three weeks.\n")


def test_a_duration_written_as_a_word_is_still_no_count(client):  # noqa: F811
    f = fields_for(W05, "three", None, None, None, metric="new case", qualifier="more_than")
    f["value"]["value"] = "3"
    item = ask(client, W05, "no new case has been recorded in this area in more than three weeks", f)
    assert item["status"] == "inconsistent"
    assert seen(item, "value", "unit") == {"value": ("match", None),
                                           "unit": ("mismatch", "unit_written_at_value:周")}
