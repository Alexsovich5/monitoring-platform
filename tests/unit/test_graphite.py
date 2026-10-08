import json

import mock
import pytest

from monplat.api import graphite
from monplat.api.app import create_app

CFG = {'database': {'zabbix_dsn': 'dbname=zabbix',
                    'monplat_dsn': 'dbname=monplat'}}
NOW = 1418000000

# (host, key_, itemid) rows as graphite.metrics() reads them.
ROWS = [
    ('mp-collector', 'mp.cpu.util', 23700),
    ('mp-collector', 'mp.fs.pused[/]', 23703),
    ('mp-collector', 'mp.load1', 23701),
    ('mp-snmp-sim', 'ifInOctets[1]', 23800),
]


def _conn(rows=ROWS):
    conn = mock.MagicMock(name='conn')
    conn.cursor.return_value.fetchall.return_value = list(rows)
    return conn


def _json(response):
    return json.loads(response.data.decode('utf-8'))


# -- paths -------------------------------------------------------------

def test_metric_path_sanitises_host_and_key():
    assert (graphite.metric_path('mp-collector', 'mp.fs.pused[/]')
            == 'zabbix.mp-collector.mp_fs_pused_')


def test_metric_path_collapses_repeated_underscores():
    assert (graphite.metric_path('Zabbix server', 'vfs.fs.size[/,pfree]')
            == 'zabbix.Zabbix_server.vfs_fs_size_pfree_')


def test_colliding_keys_get_an_itemid_suffix():
    paths = graphite.paths([('h', 'a.b', 11), ('h', 'a_b', 12),
                            ('h', 'c', 13), ('other', 'a.b', 14)])
    assert paths == {'zabbix.h.a_b_11': 11,
                     'zabbix.h.a_b_12': 12,
                     'zabbix.h.c': 13,
                     'zabbix.other.a_b': 14}


def test_metrics_reads_numeric_items_of_monitored_hosts():
    conn = _conn()
    assert graphite.metrics(conn)['zabbix.mp-collector.mp_cpu_util'] == 23700
    sql, params = conn.cursor.return_value.execute.call_args[0]
    assert 'value_type IN' in sql and 'status = %s' in sql
    assert conn.cursor.return_value.close.called


# -- find --------------------------------------------------------------

def test_find_root_returns_the_zabbix_branch():
    assert graphite.find(_conn(), '*') == [
        {'text': 'zabbix', 'id': 'zabbix', 'leaf': 0, 'expandable': 1,
         'allowChildren': 1}]


def test_find_hosts_returns_expandable_nodes():
    nodes = graphite.find(_conn(), 'zabbix.*')
    assert [n['id'] for n in nodes] == ['zabbix.mp-collector',
                                        'zabbix.mp-snmp-sim']
    for node in nodes:
        assert node['leaf'] == 0
        assert node['expandable'] == 1
        assert node['allowChildren'] == 1
        assert node['text'] == node['id'].split('.')[-1]


def test_find_items_returns_leaves():
    nodes = graphite.find(_conn(), 'zabbix.mp-collector.*')
    assert [n['text'] for n in nodes] == ['mp_cpu_util', 'mp_fs_pused_',
                                          'mp_load1']
    for node in nodes:
        assert node['leaf'] == 1
        assert node['expandable'] == 0
        assert node['allowChildren'] == 0
        assert node['id'] == 'zabbix.mp-collector.' + node['text']


def test_find_matches_partial_wildcards_and_exact_names():
    assert [n['text'] for n in
            graphite.find(_conn(), 'zabbix.mp-collector.mp_*u*')] == [
        'mp_cpu_util', 'mp_fs_pused_']
    assert [n['id'] for n in graphite.find(_conn(), 'zabbix.mp-snmp-sim')] \
        == ['zabbix.mp-snmp-sim']


def test_find_wildcard_does_not_cross_dots():
    assert graphite.find(_conn(), 'zabbix.*cpu*') == []
    assert graphite.find(_conn(), 'zabbix.mp-collector.mp_cpu_util.*') == []


# -- targets and render ------------------------------------------------

def test_parse_target_without_alias():
    assert graphite.parse_target('zabbix.h.k') == ('zabbix.h.k', None)


@pytest.mark.parametrize('target', ['alias(zabbix.h.k, "CPU")',
                                    "alias(zabbix.h.k,'CPU')",
                                    'alias( zabbix.h.k , "CPU" )'])
def test_parse_target_alias(target):
    assert graphite.parse_target(target) == ('zabbix.h.k', 'CPU')


@pytest.mark.parametrize('target', ['sumSeries(zabbix.h.*)', 'alias(x)',
                                    '', 'zabbix.h k'])
def test_unsupported_targets_raise(target):
    with pytest.raises(graphite.TargetError):
        graphite.parse_target(target)


@pytest.yield_fixture
def fetch():
    with mock.patch('monplat.api.graphite.history.fetch') as fetch:
        fetch.return_value = [(1.0, 100), (3.0, 160)]
        yield fetch


def test_render_datapoints_are_value_timestamp_pairs(fetch):
    series = graphite.render(_conn(), ['zabbix.mp-collector.mp_cpu_util'],
                             0, 200, None)
    assert series == [{'target': 'zabbix.mp-collector.mp_cpu_util',
                       'datapoints': [[1.0, 100], [3.0, 160]]}]
    assert fetch.call_args[0][1:] == (23700, 0, 200)


def test_alias_renames_the_target(fetch):
    series = graphite.render(
        _conn(), ['alias(zabbix.mp-collector.mp_cpu_util, "CPU")'],
        0, 200, None)
    assert [s['target'] for s in series] == ['CPU']


def test_render_expands_wildcards_into_one_series_per_leaf(fetch):
    series = graphite.render(_conn(), ['zabbix.mp-collector.mp_*'],
                             0, 200, None)
    assert [s['target'] for s in series] == [
        'zabbix.mp-collector.mp_cpu_util',
        'zabbix.mp-collector.mp_fs_pused_',
        'zabbix.mp-collector.mp_load1']
    assert sorted(c[0][1] for c in fetch.call_args_list) == [23700, 23701,
                                                             23703]


def test_render_unknown_path_returns_no_series(fetch):
    assert graphite.render(_conn(), ['zabbix.nohost.x'], 0, 200, None) == []
    assert not fetch.called


def test_max_data_points_reduces_points_with_bucket(fetch):
    fetch.return_value = [(float(i), 1000 + i * 10) for i in range(100)]
    with mock.patch('monplat.api.graphite.history.bucket',
                    wraps=graphite.history.bucket) as bucket:
        series = graphite.render(_conn(), ['zabbix.mp-collector.mp_load1'],
                                 1000, 1990, 10)
    assert bucket.called
    points = series[0]['datapoints']
    assert 0 < len(points) <= 10
    assert all(ts % bucket.call_args[0][1] == 0 for _, ts in points)
    # Averages keep the overall mean of evenly spread points.
    total = sum(v for v, _ in points)
    assert 0 < total / len(points) < 99


def test_max_data_points_leaves_short_series_alone(fetch):
    with mock.patch('monplat.api.graphite.history.bucket') as bucket:
        series = graphite.render(_conn(), ['zabbix.mp-collector.mp_load1'],
                                 0, 200, 500)
    assert not bucket.called
    assert series[0]['datapoints'] == [[1.0, 100], [3.0, 160]]


def test_parse_time_is_shared_with_the_rest_api():
    assert graphite.parse_time('-15min', NOW) == NOW - 900


# -- HTTP endpoints ----------------------------------------------------

@pytest.yield_fixture
def connect():
    with mock.patch('monplat.api.app.db.connect') as connect:
        connect.return_value = _conn()
        yield connect


@pytest.yield_fixture
def client(connect, fetch):
    app = create_app(CFG)
    app.testing = True
    with mock.patch('monplat.api.app.time.time', return_value=NOW):
        yield app.test_client()


def test_find_endpoint(client):
    response = client.get('/metrics/find/?query=zabbix.*')
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == '*'
    assert [n['text'] for n in _json(response)] == ['mp-collector',
                                                     'mp-snmp-sim']


def test_find_endpoint_requires_a_query(client):
    response = client.get('/metrics/find/')
    assert response.status_code == 400


def test_render_accepts_grafana_form_post(client, fetch):
    response = client.post('/render', data={
        'target': ['zabbix.mp-collector.mp_cpu_util',
                   'alias(zabbix.mp-collector.mp_load1, "Load")'],
        'from': '-15min', 'until': 'now', 'format': 'json',
        'maxDataPoints': '1000'})
    assert response.status_code == 200
    body = _json(response)
    assert [s['target'] for s in body] == ['zabbix.mp-collector.mp_cpu_util',
                                           'Load']
    assert body[0]['datapoints'] == [[1.0, 100], [3.0, 160]]
    for call in fetch.call_args_list:
        assert call[0][2:] == (NOW - 900, NOW)


def test_render_get_defaults_to_the_last_day(client, fetch):
    response = client.get('/render?target=zabbix.mp-collector.mp_cpu_util'
                          '&format=json')
    assert response.status_code == 200
    assert fetch.call_args[0][2:] == (NOW - 86400, NOW)


def test_render_png_is_400(client, fetch):
    response = client.get('/render?target=zabbix.mp-collector.mp_cpu_util'
                          '&format=png')
    assert response.status_code == 400
    assert 'json' in _json(response)['error']
    assert not fetch.called


@pytest.mark.parametrize('query', [
    'from=-5x',
    'until=yesterday',
    'from=now&until=-1h',
    'maxDataPoints=0',
    'maxDataPoints=many',
    'target=sumSeries(zabbix.*.*)',
])
def test_render_bad_parameters_are_400(client, fetch, query):
    response = client.get('/render?format=json&' + query)
    assert response.status_code == 400
    assert _json(response)['error']
    assert not fetch.called
