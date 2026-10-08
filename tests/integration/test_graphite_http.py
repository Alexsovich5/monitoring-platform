import pytest
import requests

from .conftest import wait_for

pytestmark = pytest.mark.integration

CPU_PATH = 'zabbix.mp-collector.mp_cpu_util'


def _url(cfg, path):
    return cfg['api']['url'] + path


def _healthy(cfg):
    try:
        return requests.get(_url(cfg, '/api/v1/health'),
                            timeout=10).status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope='module')
def api_up(cfg):
    wait_for(lambda: _healthy(cfg), timeout=60,
             message='api /api/v1/health answering 200')


def test_find_lists_the_collector_host_and_its_cpu_leaf(cfg, api_up):
    hosts = requests.get(_url(cfg, '/metrics/find/'),
                         params={'query': 'zabbix.*'}, timeout=10).json()
    node = [n for n in hosts if n['id'] == 'zabbix.mp-collector']
    assert node and node[0]['expandable'] == 1 and node[0]['leaf'] == 0

    leaves = requests.get(_url(cfg, '/metrics/find/'),
                          params={'query': 'zabbix.mp-collector.*'},
                          timeout=10).json()
    ids = [n['id'] for n in leaves]
    assert CPU_PATH in ids
    assert 'zabbix.mp-collector.mp_fs_pused_' in ids
    assert all(n['leaf'] == 1 for n in leaves)


def _render(cfg):
    response = requests.post(_url(cfg, '/render'), data={
        'target': CPU_PATH, 'from': '-1h', 'until': 'now',
        'format': 'json', 'maxDataPoints': '500'}, timeout=10)
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == '*'
    return response.json()


def test_render_post_returns_cpu_datapoints(cfg, api_up):
    # The collector service pushes a batch every 30 s.
    series = wait_for(
        lambda: [s for s in _render(cfg)
                 if any(v is not None for v, _ in s['datapoints'])],
        timeout=120, interval=5,
        message='non-null datapoints for %s from /render' % CPU_PATH)
    assert series[0]['target'] == CPU_PATH
    for value, ts in series[0]['datapoints']:
        assert 0.0 <= value <= 100.0
        assert isinstance(ts, int)


def test_render_png_is_rejected_over_http(cfg, api_up):
    response = requests.get(_url(cfg, '/render'),
                            params={'target': CPU_PATH, 'format': 'png'},
                            timeout=10)
    assert response.status_code == 400
