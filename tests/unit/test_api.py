import json

import mock
import psycopg2
import pytest

from monplat import history
from monplat.api.app import create_app

CFG = {'database': {'zabbix_dsn': 'dbname=zabbix',
                    'monplat_dsn': 'dbname=monplat'}}
NOW = 1418000000


def _conn(fetchone=None, fetchall=None):
    """A psycopg2 connection stand-in whose cursor answers every query
    with ``fetchone`` / ``fetchall``."""
    conn = mock.MagicMock(name='conn')
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = fetchone
    cursor.fetchall.return_value = fetchall or []
    return conn


@pytest.yield_fixture
def connect():
    with mock.patch('monplat.api.app.db.connect') as connect:
        connect.return_value = _conn()
        yield connect


@pytest.yield_fixture
def fetch():
    with mock.patch('monplat.api.app.history.fetch') as fetch:
        fetch.return_value = []
        yield fetch


@pytest.yield_fixture
def client(connect, fetch):
    app = create_app(CFG)
    app.testing = True
    with mock.patch('monplat.api.app.time.time', return_value=NOW):
        yield app.test_client()


def _json(response):
    return json.loads(response.data.decode('utf-8'))


def test_health_reports_both_databases(client, connect):
    response = client.get('/api/v1/health')
    assert response.status_code == 200
    assert _json(response) == {'zabbix_db': True, 'monplat_db': True}
    names = sorted(call[0][1] for call in connect.call_args_list)
    assert names == ['monplat', 'zabbix']


@pytest.mark.parametrize('down', ['zabbix', 'monplat'])
def test_health_is_503_when_a_database_connect_raises(client, connect, down):
    def fake_connect(cfg, name='zabbix'):
        if name == down:
            raise psycopg2.OperationalError('could not connect')
        return _conn()
    connect.side_effect = fake_connect
    response = client.get('/api/v1/health')
    assert response.status_code == 503
    body = _json(response)
    assert body['%s_db' % down] is False
    assert all(v is True for k, v in body.items() if k != '%s_db' % down)


def test_hosts_lists_monitored_hosts(client, connect):
    connect.return_value = _conn(fetchall=[(10084, 'Zabbix server'),
                                           (10105, 'mp-collector')])
    response = client.get('/api/v1/hosts')
    assert response.status_code == 200
    assert _json(response) == [{'hostid': 10084, 'host': 'Zabbix server'},
                               {'hostid': 10105, 'host': 'mp-collector'}]
    sql, params = connect.return_value.cursor.return_value.execute.call_args[0]
    assert 'status = %s' in sql and 'flags IN (%s, %s)' in sql
    assert params == (0, 0, 4)


def test_unknown_host_returns_404(client, connect):
    connect.return_value = _conn(fetchone=None)
    response = client.get('/api/v1/hosts/no-such-host/items')
    assert response.status_code == 404
    assert 'no-such-host' in _json(response)['error']


def test_host_items_are_listed(client, connect):
    conn = _conn(fetchone=(10105,),
                 fetchall=[(23700, 'mp.cpu.util', 'CPU utilisation', '%', 0),
                           (23701, 'mp.net.in.bytes', 'Network bytes received',
                            'B', 3)])
    connect.return_value = conn
    response = client.get('/api/v1/hosts/mp-collector/items')
    assert response.status_code == 200
    assert _json(response) == [
        {'itemid': 23700, 'key': 'mp.cpu.util', 'name': 'CPU utilisation',
         'units': '%', 'value_type': 0},
        {'itemid': 23701, 'key': 'mp.net.in.bytes',
         'name': 'Network bytes received', 'units': 'B', 'value_type': 3}]
    executed = conn.cursor.return_value.execute.call_args_list
    lookup_sql, lookup_params = executed[0][0]
    assert 'flags IN (%s, %s)' in lookup_sql
    assert lookup_params == ('mp-collector', 0, 0, 4)
    assert executed[1][0][1] == (10105,)


def test_history_parses_relative_range_and_step(client, fetch):
    fetch.return_value = [(1.5, NOW - 120), (2.5, NOW - 60)]
    response = client.get('/api/v1/items/23700/history'
                          '?from=-15min&until=now&step=60')
    assert response.status_code == 200
    assert _json(response) == [{'clock': NOW - 120, 'value': 1.5},
                               {'clock': NOW - 60, 'value': 2.5}]
    args, kwargs = fetch.call_args
    assert args[1:] == (23700, NOW - 900, NOW)
    assert kwargs == {'step': 60}


def test_history_defaults_to_last_hour_without_bucketing(client, fetch):
    response = client.get('/api/v1/items/23700/history')
    assert response.status_code == 200
    args, kwargs = fetch.call_args
    assert args[1:] == (23700, NOW - 3600, NOW)
    assert kwargs == {'step': None}


def test_history_accepts_epoch_bounds(client, fetch):
    client.get('/api/v1/items/23700/history?from=1417990000&until=1417995000')
    assert fetch.call_args[0][1:] == (23700, 1417990000, 1417995000)


@pytest.mark.parametrize('query', [
    'from=-5x',
    'from=yesterday',
    'until=-1q',
    'step=abc',
    'step=0',
    'step=-60',
    'from=now&until=-1h',
])
def test_bad_history_parameters_return_400(client, fetch, query):
    response = client.get('/api/v1/items/23700/history?' + query)
    assert response.status_code == 400
    assert _json(response)['error']
    assert not fetch.called


def test_unknown_item_history_returns_404(client, fetch):
    fetch.side_effect = history.UnknownItem('no item with itemid 1')
    response = client.get('/api/v1/items/1/history')
    assert response.status_code == 404


def test_non_numeric_item_history_returns_400(client, fetch):
    fetch.side_effect = history.UnsupportedValueType('value_type 4')
    response = client.get('/api/v1/items/1/history')
    assert response.status_code == 400


@pytest.mark.parametrize('path', ['/api/v1/health', '/api/v1/hosts',
                                  '/api/v1/hosts/x/items',
                                  '/api/v1/items/1/history?from=-5x',
                                  '/api/v1/nowhere'])
def test_cors_header_is_present(client, path):
    response = client.get(path)
    assert response.headers.get('Access-Control-Allow-Origin') == '*'


def test_connections_are_closed(client, connect):
    conn = _conn(fetchall=[])
    connect.return_value = conn
    client.get('/api/v1/hosts')
    assert conn.close.called


def test_create_app_loads_config_when_none_given():
    with mock.patch('monplat.api.app.config.load', return_value=CFG) as load:
        app = create_app()
    assert load.called
    assert app.config['MONPLAT'] is CFG
