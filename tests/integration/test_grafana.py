import pytest
import requests

from .conftest import wait_for

pytestmark = pytest.mark.integration

GRAFANA = 'http://grafana:3000/'


def _get(path):
    return requests.get(GRAFANA + path, timeout=10)


def _serving():
    try:
        return _get('index.html').status_code == 200
    except requests.RequestException:
        return False


@pytest.fixture(scope='module')
def grafana_up():
    wait_for(_serving, timeout=60, message='grafana answering on :3000')


def test_index_html_is_served(grafana_up):
    response = _get('index.html')
    assert response.status_code == 200
    assert 'css/grafana.dark.min.css' in response.text


def test_compiled_dark_css_is_served(grafana_up):
    response = _get('css/grafana.dark.min.css')
    assert response.status_code == 200
    # Compiled from bootstrap.dark.less: the LESS was really built.
    assert '.navbar' in response.text
    assert '@import' not in response.text


def test_config_points_graphite_at_the_api(grafana_up):
    response = _get('config.js')
    assert response.status_code == 200
    assert "type: 'graphite'" in response.text
    assert '/dashboard/file/mp-linux.json' in response.text


def test_dashboard_targets_resolve_via_metrics_find(cfg, grafana_up):
    response = _get('app/dashboards/mp-linux.json')
    assert response.status_code == 200
    dashboard = response.json()
    assert dashboard['version'] == 6
    targets = [t['target'] for row in dashboard['rows']
               for panel in row['panels'] for t in panel['targets']]
    assert 'zabbix.mp-collector.mp_cpu_util' in targets
    for target in targets:
        nodes = requests.get(cfg['api']['url'] + '/metrics/find/',
                             params={'query': target}, timeout=10).json()
        assert [n['id'] for n in nodes if n['leaf'] == 1] == [target]
