from scripts.engine.constants import format_number

def test_format_number():
    assert format_number(None) == "0"
    assert format_number("abc") == "0"
    assert format_number(1_500_000_000) == "1.5B"
    assert format_number(1_000_000_000) == "1B"
    assert format_number(1_500_000) == "1.5M"
    assert format_number(1_000_000) == "1M"
    assert format_number(15_000) == "15K"
    assert format_number(10_000) == "10K"
    assert format_number(1.5) == "1.5"
    assert format_number(9999) == "9,999"
