import pytest

from monplat.timeparse import TimeParseError, parse_time

NOW = 1418000000


@pytest.mark.parametrize('text,expected', [
    ('-5min', NOW - 5 * 60),
    ('-15min', NOW - 15 * 60),
    ('-30s', NOW - 30),
    ('-6h', NOW - 6 * 3600),
    ('-7d', NOW - 7 * 86400),
    ('-2w', NOW - 2 * 7 * 86400),
    ('-1mon', NOW - 30 * 86400),
    ('-1y', NOW - 365 * 86400),
    ('-5m', NOW - 5 * 60),
    ('now', NOW),
    ('1417990000', 1417990000),
])
def test_parse_time_accepts_graphite_forms(text, expected):
    assert parse_time(text, NOW) == expected


@pytest.mark.parametrize('text', ['-5x', '', 'yesterday', '-min', '5min',
                                  '-5 min', '12.5', None])
def test_parse_time_rejects_other_forms(text):
    with pytest.raises(TimeParseError):
        parse_time(text, NOW)


def test_time_parse_error_is_a_value_error():
    assert issubclass(TimeParseError, ValueError)
