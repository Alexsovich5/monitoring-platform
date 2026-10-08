import time

import pytest
import requests

from .conftest import wait_for

pytestmark = pytest.mark.integration

COLLECTOR_HOST = 'mp-collector'
CPU_KEY = 'mp.cpu.util'


def _get(cfg, path, **params):
    return requests.get(cfg['api']['url'] + path, params=params, timeout=10)


def _healthy(cfg):
    try:
        return _get(cfg, '/api/v1/health').status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope='module')
def api_up(cfg):
    wait_for(lambda: _healthy(cfg), timeout=60,
             message='api /api/v1/health answering 200')


def test_hosts_include_the_collector_host(cfg, api_up):
    response = _get(cfg, '/api/v1/hosts')
    assert response.status_code == 200
    assert response.headers['Access-Control-Allow-Origin'] == '*'
    hosts = [h['host'] for h in response.json()]
    assert COLLECTOR_HOST in hosts
    prototypes = [h for h in hosts if h.startswith('{#')]
    assert prototypes == []


def test_host_prototype_items_are_not_served(cfg, api_up):
    response = _get(cfg, '/api/v1/hosts/%s/items' % '{%23HV.UUID}')
    assert response.status_code == 404


def test_collector_cpu_history_is_served(cfg, api_up):
    response = _get(cfg, '/api/v1/hosts/%s/items' % COLLECTOR_HOST)
    assert response.status_code == 200
    items = dict((i['key'], i) for i in response.json())
    assert CPU_KEY in items, sorted(items)
    itemid = items[CPU_KEY]['itemid']

    # The collector service pushes a batch every 30 s.
    points = wait_for(
        lambda: _get(cfg, '/api/v1/items/%s/history' % itemid,
                     **{'from': '-1h', 'until': 'now'}).json(),
        timeout=120, interval=5,
        message='%s history for %s via the API' % (CPU_KEY, COLLECTOR_HOST))
    now = time.time()
    for point in points:
        assert 0.0 <= point['value'] <= 100.0
        assert now - 3600 - 5 <= point['clock'] <= now + 5

    bucketed = _get(cfg, '/api/v1/items/%s/history' % itemid,
                    **{'from': '-1h', 'step': '300'}).json()
    assert bucketed and all(p['clock'] % 300 == 0 for p in bucketed)


def test_unknown_host_is_404_over_http(cfg, api_up):
    response = _get(cfg, '/api/v1/hosts/no-such-host/items')
    assert response.status_code == 404
