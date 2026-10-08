"""A breaching trapper value goes through a Zabbix trigger and action,
``mp_alert.py`` and ``POST /api/v1/events`` into ``monplat.events``."""
import time

import pytest
import requests

from monplat import db, events

from .conftest import send_until_processed, wait_for

pytestmark = pytest.mark.integration

HOST = 'mp-test-alert'
CPU_KEY = 'mp.cpu.util'
TRIGGER_PREFIX = 'High CPU'
TIMEOUT = 120


def _events(cfg, status):
    try:
        response = requests.get(cfg['api']['url'] + '/api/v1/events',
                                params={'host': HOST, 'status': status},
                                timeout=10)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    return [e for e in response.json()
            if e['trigger_name'].startswith(TRIGGER_PREFIX)]


def test_provisioned_action_and_media(zapi):
    found = zapi.action.get(filter={'name': 'MP notify API'},
                            output='extend', selectFilter='extend')
    assert len(found) == 1
    action = found[0]
    assert action['recovery_msg'] == '1'
    assert action['status'] == '0'
    conditions = [(c['conditiontype'], c['operator'], c['value'])
                  for c in action['filter']['conditions']]
    assert conditions == [('5', '0', '1')]
    mediatypes = zapi.mediatype.get(filter={'description': 'MP API'},
                                    output='extend')
    assert [(m['type'], m['exec_path']) for m in mediatypes] == \
        [('1', 'mp_alert.py')]


def test_store_keeps_one_row_per_eventid_and_status(cfg):
    body = '\n'.join(['eventid=990001', 'status=PROBLEM',
                      'host=mp-store-check', 'trigger_id=1',
                      'trigger_name=store check', 'severity=2',
                      'value=1', 'time=2014.12.18 12:00:00'])
    event = events.parse_alert('PROBLEM: store check', body)
    conn = db.connect(cfg, 'monplat')
    try:
        first = events.store(conn, event)
        second = events.store(conn, event)
        cursor = conn.cursor()
        cursor.execute('SELECT count(*) FROM events '
                       'WHERE eventid = %s AND status = %s',
                       (990001, 'PROBLEM'))
        count = cursor.fetchone()[0]
        cursor.close()
    finally:
        conn.close()
    assert first == second
    assert count == 1


def test_high_cpu_problem_and_recovery_are_recorded(cfg, test_hosts):
    send_until_processed(cfg, [(HOST, CPU_KEY, 99, None)])
    problems = wait_for(lambda: _events(cfg, 'PROBLEM'), TIMEOUT,
                        message='PROBLEM event for %s recorded' % HOST)
    assert len(problems) == 1
    problem = problems[0]
    assert problem['host'] == HOST
    assert problem['severity'] == 4
    assert problem['trigger_name'] == 'High CPU on %s' % HOST

    # avg(5m) of 99 and 5 is 52, below the trigger's 90.
    send_until_processed(cfg, [(HOST, CPU_KEY, 5, None)])
    oks = wait_for(lambda: _events(cfg, 'OK'), TIMEOUT,
                   message='OK event for %s recorded' % HOST)
    # Give a duplicate notification time to arrive before counting.
    time.sleep(10)
    oks = _events(cfg, 'OK')
    assert len(oks) == 1
    assert oks[0]['eventid'] == problem['eventid']
    assert len(_events(cfg, 'PROBLEM')) == 1
