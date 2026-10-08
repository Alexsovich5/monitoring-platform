import mock
import pytest

from monplat import cli, forecast

HOUR = 3600
NOW = 1400000000


def close(actual, expected, tol=1e-6):
    return actual is not None and abs(actual - expected) <= tol


def rising(count, start=70.0, per_hour=1.0, end=NOW):
    """``count`` hourly points ending at ``end``, rising ``per_hour``."""
    return [(start + per_hour * i, end - (count - 1 - i) * HOUR)
            for i in range(count)]


def test_fit_exact_line():
    points = [(2.0 * x + 1, x) for x in range(6)]
    slope, intercept = forecast.fit(points)
    assert close(slope, 2.0)
    assert close(intercept, 1.0)


def test_fit_needs_two_distinct_clocks():
    with pytest.raises(ValueError):
        forecast.fit([(1.0, 5), (2.0, 5)])


def test_hours_none_for_flat_series():
    points = [(70.0, NOW - i * HOUR) for i in range(6)]
    assert forecast.hours_to_threshold(points, 90, NOW) is None


def test_hours_none_for_falling_series():
    points = rising(6, start=80.0, per_hour=-1.0)
    assert forecast.hours_to_threshold(points, 90, NOW) is None


def test_hours_none_for_fewer_than_three_points():
    assert forecast.hours_to_threshold(rising(2), 90, NOW) is None
    assert forecast.hours_to_threshold([], 90, NOW) is None


def test_hours_zero_when_threshold_already_crossed():
    points = rising(5, start=88.0)
    assert forecast.hours_to_threshold(points, 90, NOW) == 0


def test_hours_for_known_slope():
    # 70 % .. 81 % at 1 %/h, last point now: 9 h until 90 %.
    points = rising(12)
    assert close(forecast.hours_to_threshold(points, 90, NOW), 9.0)


def test_hours_measured_from_now_not_last_point():
    # Two %/h, last point (80 %) one hour ago: the line is at 82 % now.
    points = rising(6, start=70.0, per_hour=2.0, end=NOW - HOUR)
    assert close(forecast.hours_to_threshold(points, 90, NOW), 4.0)


CFG = {
    'zabbix': {'server': 'zbx.example', 'port': 10051},
    'database': {'zabbix_dsn': 'dbname=zabbix'},
    'forecast': {
        'window_hours': 6,
        'horizon_hours': 24,
        'items': [
            {'host': 'h1', 'key': 'mp.fs.pused[/]', 'threshold': 90,
             'target_key': 'mp.forecast.hours_left[/]'},
            {'host': 'h2', 'key': 'mp.fs.pused[/]', 'threshold': 80,
             'target_key': 'mp.forecast.hours_left[/]'},
        ],
    },
}


def test_run_once_sends_one_value_per_item():
    conn = mock.Mock()
    itemids = {('h1', 'mp.fs.pused[/]'): 11, ('h2', 'mp.fs.pused[/]'): 22}
    series = {11: rising(7), 22: rising(7, start=60.0, per_hour=2.0)}
    windows = []

    def fetch(c, itemid, start, end):
        windows.append((itemid, start, end))
        return series[itemid]

    send = mock.Mock(return_value={'processed': 2, 'failed': 0, 'total': 2})
    result = forecast.run(CFG, once=True, connect=lambda cfg: conn,
                          lookup=lambda c, h, k: itemids[(h, k)],
                          fetch=fetch, send=send, now=lambda: NOW)
    assert result == {'processed': 2, 'failed': 0, 'total': 2}
    assert send.call_count == 1
    server, port, rows = send.call_args[0]
    assert (server, port) == ('zbx.example', 10051)
    assert len(rows) == 2
    by_host = dict((row[0], row) for row in rows)
    assert by_host['h1'][1] == 'mp.forecast.hours_left[/]'
    assert close(by_host['h1'][2], 14.0)   # 76 % now, 90 % at 1 %/h
    assert close(by_host['h2'][2], 4.0)    # 72 % now, 80 % at 2 %/h
    assert all(row[3] == NOW for row in rows)
    assert sorted(windows) == [(11, NOW - 6 * HOUR, NOW),
                               (22, NOW - 6 * HOUR, NOW)]
    conn.close.assert_called_once_with()


def test_run_once_skips_items_without_a_forecast():
    send = mock.Mock(return_value={'processed': 1, 'failed': 0, 'total': 1})
    series = {1: rising(7), 2: [(50.0, NOW - i * HOUR) for i in range(7)]}
    ids = {'h1': 1, 'h2': 2}
    forecast.run(CFG, once=True, connect=lambda cfg: mock.Mock(),
                 lookup=lambda c, h, k: ids[h],
                 fetch=lambda c, i, s, e: series[i], send=send,
                 now=lambda: NOW)
    rows = send.call_args[0][2]
    assert [row[0] for row in rows] == ['h1']


def test_run_once_skips_unknown_items():
    send = mock.Mock(return_value={'processed': 1, 'failed': 0, 'total': 1})
    forecast.run(CFG, once=True, connect=lambda cfg: mock.Mock(),
                 lookup=lambda c, h, k: 5 if h == 'h2' else None,
                 fetch=lambda c, i, s, e: rising(7), send=send,
                 now=lambda: NOW)
    rows = send.call_args[0][2]
    assert [row[0] for row in rows] == ['h2']


def test_cli_forecast_once_prints_sender_result():
    result = {'processed': 1, 'failed': 0, 'total': 1}
    with mock.patch('monplat.config.load', return_value=CFG), \
            mock.patch.object(forecast, 'run',
                              return_value=result) as run:
        assert cli.main(['forecast', '--once']) == 0
    assert run.call_args[1]['once'] is True


def test_cli_forecast_interval_option():
    args = cli.build_parser().parse_args(['forecast', '--interval', '300'])
    assert args.interval == 300 and not args.once
