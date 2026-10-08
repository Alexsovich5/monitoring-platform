"""Credentials must not appear in exceptions, CLI output or HTTP
responses when the things they unlock fail."""
import logging

import mock
import pytest
import requests
from pyzabbix import ZabbixAPIException

from monplat import cli, events, notify
from monplat.api.app import create_app
from monplat.zabbix import api

SENTINEL = 'S3NTINEL-pw'
# Nothing listens on port 1, so connections are refused at once.
DEAD_DSN = 'host=127.0.0.1 port=1 dbname=x user=x password=%s' % SENTINEL
CFG = {'zabbix': {'url': 'http://127.0.0.1:1/zabbix', 'user': 'Admin',
                  'password': SENTINEL, 'server': '127.0.0.1', 'port': 1},
       'database': {'zabbix_dsn': DEAD_DSN, 'monplat_dsn': DEAD_DSN},
       'notify': {'pushover_url': 'http://127.0.0.1:1/1/messages.json',
                  'token': SENTINEL, 'user': SENTINEL}}


@pytest.yield_fixture
def captured_logs():
    records = []

    class Keep(logging.Handler):
        def emit(self, record):
            records.append(self.format(record))

    handler = Keep()
    root = logging.getLogger()
    old_level = root.level
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield records
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)


@pytest.mark.parametrize('error', [
    requests.ConnectionError('connection refused'),
    ZabbixAPIException('Received empty response'),
])
def test_unreachable_zabbix_error_does_not_contain_the_password(
        error, captured_logs):
    with mock.patch('monplat.zabbix.api.ZabbixAPI') as zabbix_api:
        zabbix_api.return_value.login.side_effect = error
        with pytest.raises(api.ZabbixUnavailable) as exc:
            api.connect(CFG, retries=2, delay=0)
    assert SENTINEL not in str(exc.value)
    assert SENTINEL not in '\n'.join(captured_logs)


def test_real_login_failure_does_not_contain_the_password(captured_logs):
    with pytest.raises(api.ZabbixUnavailable) as exc:
        api.connect(CFG, retries=1, delay=0, timeout=2)
    assert SENTINEL not in str(exc.value)
    assert SENTINEL not in '\n'.join(captured_logs)


def test_provision_failure_output_has_no_password(monkeypatch, capsys,
                                                  captured_logs):
    monkeypatch.setenv('MONPLAT_ZABBIX_URL', 'http://127.0.0.1:1/zabbix')
    monkeypatch.setenv('MONPLAT_ZABBIX_PASSWORD', SENTINEL)
    with mock.patch('monplat.zabbix.api.time.sleep'):
        code = cli.main(['provision'])
    assert code == 1
    out, err = capsys.readouterr()
    assert SENTINEL not in out + err
    assert SENTINEL not in '\n'.join(captured_logs)


def test_forecast_failure_output_has_no_database_password(monkeypatch,
                                                          capsys):
    monkeypatch.setenv('MONPLAT_DATABASE_ZABBIX_DSN', DEAD_DSN)
    code = cli.main(['forecast', '--once'])
    assert code == 1
    out, err = capsys.readouterr()
    assert 'mpctl forecast' in err
    assert SENTINEL not in out + err


@pytest.mark.parametrize('path', ['/api/v1/health', '/api/v1/hosts',
                                  '/metrics/find/?query=zabbix.*',
                                  '/api/v1/events'])
def test_database_failures_do_not_reach_http_responses(path):
    app = create_app(CFG)
    response = app.test_client().get(path)
    assert response.status_code in (500, 503)
    assert SENTINEL not in response.data.decode('utf-8')
    assert 'password' not in response.data.decode('utf-8')


def test_failed_push_does_not_print_the_push_token(capsys, captured_logs):
    event = events.parse_alert('PROBLEM', (
        'eventid=1\nstatus=PROBLEM\nhost=h\ntrigger_id=2\n'
        'trigger_name=t\nseverity=4\ntime=2014.12.18 12:00:00\nvalue=1\n'))
    assert notify.push(CFG, event) is False
    out, err = capsys.readouterr()
    assert 'push to' in err
    assert SENTINEL not in out + err
    assert SENTINEL not in '\n'.join(captured_logs)
