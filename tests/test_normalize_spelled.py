"""extraction_normalize.v6: a count written as an English word of one to twelve is a number, whole; v1 is unchanged.
The sentences are those of the WHO notices of the D1 measurement batch."""

import pytest

from app import normalize as n

ITALY = "Italy has reported seven cases of E-11 infection confirmed in neonates between April and June 2023"
FRANCE = "Of the nine cases, there were seven reported deaths (Case Fatality Ratio (CFR) = 78%)"
MARBURG = "Twelve of the confirmed cases died"


@pytest.mark.parametrize("written,text", [("nine", "9"), ("Seven", "7"), ("TWELVE", "12"), (" two ", "2"),
                                          ("one", "1"), ("8,500", "8500"), ("9 029", "9029")])
def test_a_spelled_number_has_a_decimal_text(written, text):
    assert n.decimal_text_v6(written) == text


@pytest.mark.parametrize("written", ["thirteen", "twenty-one", "nine cases", "", None, "ninety", "first"])
def test_anything_else_has_none(written):
    assert n.decimal_text_v6(written) is None


def test_v1_does_not_read_the_word():
    assert n.decimal_text("nine") is None and not n.is_whole_number(ITALY, 19, 24)


@pytest.mark.parametrize("text,word", [(ITALY, "seven"), (FRANCE, "nine"), (FRANCE, "seven"), (MARBURG, "Twelve")])
def test_a_spelled_number_is_whole_as_a_word(text, word):
    at = text.index(word)
    assert n.is_whole_number_v6(text, at, at + len(word))


@pytest.mark.parametrize("text,word", [
    ("none of the cases", "one"), ("someone reported", "one"), ("one-third of the cases", "one"),
    ("twenty-one cases", "one"), ("one's own", "one"), ("seventeen cases", "seven"), ("Marburg", "Mar")])
def test_a_part_of_a_longer_word_is_not_a_number(text, word):
    at = text.index(word)
    assert not n.is_whole_number_v6(text, at, at + len(word))


def test_digits_are_whole_as_in_v1():
    text = "a total of 8,500 suspected cases"
    at = text.index("8,500")
    assert n.is_whole_number_v6(text, at, at + 5) and not n.is_whole_number_v6(text, at + 2, at + 5)
