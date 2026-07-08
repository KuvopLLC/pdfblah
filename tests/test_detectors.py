from pdfblah.detectors import DETECTORS, find_all
import re


def match(name, text):
    got = find_all(text, [name])
    return got[0][1] if got else None


def test_email():
    assert match("email", "reach me at a.b+c@example.co.uk please") == "a.b+c@example.co.uk"
    assert match("email", "no address here") is None


def test_iban_valid_and_checksum():
    assert match("iban", "IBAN DE89370400440532013000 end") == "DE89370400440532013000"
    assert match("iban", "pay to DE89 3704 0044 0532 0130 00 now") == "DE89 3704 0044 0532 0130 00"
    assert match("iban", "DE00370400440532013000") is None   # bad checksum rejected


def test_credit_card_luhn():
    assert match("credit_card", "card 4242 4242 4242 4242 ok") == "4242 4242 4242 4242"
    assert match("credit_card", "4111111111111111") == "4111111111111111"
    assert match("credit_card", "4242 4242 4242 4241") is None   # bad Luhn
    assert match("credit_card", "order 1234 5678 number") is None


def test_ssn():
    assert match("ssn", "SSN 123-45-6789") == "123-45-6789"
    assert match("ssn", "000-45-6789") is None
    assert match("ssn", "666-45-6789") is None
    assert match("ssn", "900-45-6789") is None
    assert match("ssn", "123-00-6789") is None


def test_phone():
    assert match("phone", "call +1 (555) 123-4567 today") == "+1 (555) 123-4567"
    assert match("phone", "ring 555-123-4567") == "555-123-4567"
    assert match("phone", "id 12345678 here") is None            # no separators


def test_no_false_positives_on_prose():
    prose = "The quick brown fox jumps over the lazy dog. Chapter 3 begins."
    assert find_all(prose, ["email", "iban", "credit_card", "ssn"]) == []


def test_find_all_multiple():
    text = "email x@y.com card 4242 4242 4242 4242 ssn 123-45-6789"
    got = {t for (t, _v, _s, _e) in find_all(text, ["email", "credit_card", "ssn"])}
    assert got == {"email", "credit_card", "ssn"}
