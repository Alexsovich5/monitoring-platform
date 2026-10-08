from decimal import Decimal

import mock
import pytest

from monplat import db, history


def test_bucket_averages_points_into_aligned_buckets():
    points = [(1.0, 60), (3.0, 119), (10.0, 125), (5.0, 300), (7.0, 359)]
    assert history.bucket(points, 60) == [(2.0, 60), (10.0, 120),
                                          (6.0, 300)]


def test_bucket_drops_empty_buckets_and_keeps_order():
    points = [(4.0, 1000), (8.0, 1010), (2.0, 4000)]
    result = history.bucket(points, 300)
    assert result == [(6.0, 900), (2.0, 3900)]
    assert [clock for _, clock in result] == sorted(c for _, c in result)


def test_bucket_of_no_points_is_empty():
    assert history.bucket([], 60) == []


@pytest.mark.parametrize('step', [0, -60])
def test_bucket_rejects_non_positive_step(step):
    with pytest.raises(ValueError):
        history.bucket([(1.0, 60)], step)


def test_value_type_selects_history_table():
    assert history.table_for(0) == 'history'
    assert history.table_for(3) == 'history_uint'


@pytest.mark.parametrize('value_type', [1, 2, 4, 5, None])
def test_other_value_types_raise(value_type):
    with pytest.raises(history.UnsupportedValueType):
        history.table_for(value_type)


def _conn(value_type_row, rows):
    cursor = mock.MagicMock()
    cursor.fetchone.return_value = value_type_row
    cursor.fetchall.return_value = rows
    conn = mock.MagicMock()
    conn.cursor.return_value = cursor
    return conn, cursor


def test_fetch_reads_uint_table_and_returns_floats_in_order():
    conn, cursor = _conn((3,), [(Decimal('5'), 100), (Decimal('7'), 160)])
    assert history.fetch(conn, 42, 0, 200) == [(5.0, 100), (7.0, 160)]
    sql, params = cursor.execute.call_args_list[-1][0]
    assert 'FROM history_uint ' in sql
    assert 'ORDER BY clock' in sql
    assert params == (42, 0, 200)


def test_fetch_buckets_when_step_given():
    conn, _ = _conn((0,), [(Decimal('1.5'), 100), (Decimal('2.5'), 110),
                           (Decimal('9'), 190)])
    assert history.fetch(conn, 7, 0, 200, step=60) == [(2.0, 60),
                                                       (9.0, 180)]


def test_fetch_unknown_item_raises():
    conn, _ = _conn(None, [])
    with pytest.raises(history.UnknownItem):
        history.fetch(conn, 999, 0, 10)


def test_db_connect_uses_configured_dsn():
    cfg = {'database': {'zabbix_dsn': 'host=a dbname=z',
                        'monplat_dsn': 'host=a dbname=m'}}
    with mock.patch.object(db.psycopg2, 'connect') as connect:
        db.connect(cfg)
        connect.assert_called_with('host=a dbname=z')
        db.connect(cfg, 'monplat')
        connect.assert_called_with('host=a dbname=m')
