"""A large file in the demo spool fires "Spool directory too large", the
API runs the "MP clear spool" global script on the Zabbix server's agent,
the run is recorded in ``monplat.remediations`` and the trigger recovers.

``/var/spool/mp-demo`` is a named volume mounted in both the ``zabbix``
container (where the agent polls and deletes) and this test runner."""
import datetime
import os
import threading

import pytest
import mock
import requests

from monplat import db, events, remediation

from .conftest import STOCK_HOST, intake_headers, wait_for

pytestmark = pytest.mark.integration

SPOOL = '/var/spool/mp-demo'
BLOB = os.path.join(SPOOL, 'blob')
TEMPLATE = 'Template MP Spool'
SCRIPT = 'MP clear spool'
TRIGGER = 'Spool directory too large on %s' % STOCK_HOST
TIMEOUT = 180


def _write_blob(size):
    with open(BLOB, 'wb') as handle:
        chunk = b'\0' * (1024 * 1024)
        for _ in range(size // len(chunk)):
            handle.write(chunk)
    os.chmod(BLOB, 0o666)


def _remediations(cfg, ok, ran=True):
    conn = db.connect(cfg, 'monplat')
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT r.rule, r.script_name, r.output, e.trigger_name'
                       ' FROM remediations r JOIN events e'
                       ' ON e.id = r.event_id'
                       ' WHERE r.host = %s AND r.ok = %s AND r.ran = %s'
                       ' ORDER BY r.id', (STOCK_HOST, ok, ran))
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    return rows


def _events(cfg, status):
    try:
        response = requests.get(cfg['api']['url'] + '/api/v1/events',
                                params={'host': STOCK_HOST,
                                        'status': status}, timeout=10)
    except requests.RequestException:
        return []
    if response.status_code != 200:
        return []
    return [e for e in response.json() if e['trigger_name'] == TRIGGER]


def test_global_script_runs_on_the_agent(zapi):
    found = zapi.script.get(filter={'name': SCRIPT}, output='extend')
    assert len(found) == 1
    script = found[0]
    assert script['execute_on'] == '0'
    assert script['host_access'] == '3'
    assert script['command'] == ('rm -f /var/spool/mp-demo/* '
                                 '&& echo cleared')


def test_spool_template_is_linked_to_the_zabbix_server_host(zapi):
    hosts = zapi.host.get(filter={'host': STOCK_HOST},
                          selectParentTemplates=['host'], output=['host'])
    linked = [t['host'] for t in hosts[0]['parentTemplates']]
    assert TEMPLATE in linked
    # Linked with host.massadd, so the stock template stays linked too.
    assert 'Template OS Linux' in linked


FORGED_TRIGGER = 'Spool directory too large (forged) on %s' % STOCK_HOST


def test_forged_event_runs_no_script_and_is_audited(cfg, zapi):
    """A well-formed, authenticated notification for a real host whose
    event does not exist in Zabbix is refused before script.execute."""
    marker = os.path.join(SPOOL, 'forged-marker')
    with open(marker, 'w') as handle:
        handle.write('still here')
    os.chmod(marker, 0o666)
    body = '\n'.join([
        'eventid=987654321', 'status=PROBLEM', 'host=%s' % STOCK_HOST,
        'trigger_id=1', 'trigger_name=%s' % FORGED_TRIGGER, 'severity=5',
        'time=2014.12.18 12:00:00', 'value=10485760'])
    response = requests.post(cfg['api']['url'] + '/api/v1/events',
                             data={'subject': 'PROBLEM', 'body': body},
                             headers=intake_headers(cfg), timeout=60)
    assert response.status_code == 201
    outcome = response.json()['remediation']
    assert outcome['ran'] is False
    assert 'not found in Zabbix' in outcome['output']
    assert os.path.exists(marker)
    refused = [row for row in _remediations(cfg, False, ran=False)
               if row[3] == FORGED_TRIGGER]
    assert len(refused) == 1
    assert refused[0][2].startswith('refused: ')
    os.remove(marker)


def test_large_spool_file_is_removed_by_remediation(cfg, zapi):
    assert os.path.isdir(SPOOL)
    assert _remediations(cfg, True) == []
    _write_blob(10 * 1024 * 1024)

    runs = wait_for(lambda: _remediations(cfg, True), TIMEOUT,
                    message='successful remediation for %s' % STOCK_HOST)
    assert len(runs) == 1
    rule, script, output, trigger = runs[0]
    assert (rule, script, trigger) == ('clear-spool', SCRIPT, TRIGGER)
    assert 'cleared' in output
    assert not os.path.exists(BLOB)
    assert _remediations(cfg, False) == []
    problems = _events(cfg, 'PROBLEM')
    assert len(problems) == 1
    assert problems[0]['severity'] == 2

    # vfs.file.size of a missing file is unsupported, which leaves the
    # trigger in PROBLEM; an empty file lets the item report 0 again.
    _write_blob(0)
    oks = wait_for(lambda: _events(cfg, 'OK'), TIMEOUT,
                   message='OK event for %r recorded' % TRIGGER)
    assert len(oks) == 1
    assert oks[0]['eventid'] == problems[0]['eventid']
    # The recovery did not start another run.
    assert len(_remediations(cfg, True)) == 1


def test_concurrent_duplicates_run_the_script_once_on_postgres(cfg):
    """Two connections deliver one event at once; the claim row and the
    advisory lock leave exactly one script.execute."""
    rules = remediation.load_rules(
        os.path.join(os.path.dirname(__file__), 'remediation.yml'))
    host = 'race-host'
    now = datetime.datetime.now(events.UTC)
    event = events.Event(eventid=424242, status='PROBLEM', host=host,
                         trigger_id=13600,
                         trigger_name='Spool directory too large on race',
                         severity=2, item_value='1', event_time=now)
    conn = db.connect(cfg, 'monplat')
    event_id = events.store(conn, event)
    conn.close()
    zapi = mock.MagicMock()
    zapi.event.get.return_value = [{
        'eventid': '424242', 'source': '0', 'object': '0',
        'objectid': '13600', 'value': '1', 'acknowledged': '0',
        'clock': '1000', 'hosts': [{'hostid': '1', 'host': host}]}]
    zapi.trigger.get.return_value = [{
        'triggerid': '13600', 'value': '1', 'lastchange': '1000',
        'priority': '2',
        'description': 'Spool directory too large on race'}]
    zapi.host.get.return_value = [{'hostid': '1', 'host': host,
                                   'groups': [{'name': 'Linux servers'}]}]
    zapi.script.get.return_value = [{'scriptid': '5', 'name': SCRIPT}]
    started = threading.Event()

    def slow_execute(**kwargs):
        started.set()
        threading.Event().wait(1)
        return {'response': 'success', 'value': 'cleared\n'}
    zapi.script.execute.side_effect = slow_execute
    rules = [r._replace(allow_hosts=(host,)) for r in rules]
    results = []

    def deliver():
        results.append(remediation.remediate(
            zapi, db.connect(cfg, 'monplat'), event, rules, event_id,
            now=now))
    threads = [threading.Thread(target=deliver) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(30)
    assert zapi.script.execute.call_count == 1
    assert sorted(r['ran'] for r in results) == [False, True]
    conn = db.connect(cfg, 'monplat')
    cursor = conn.cursor()
    cursor.execute('SELECT ok, output FROM remediations'
                   ' WHERE event_id = %s AND ran', (event_id,))
    assert cursor.fetchall() == [(True, 'cleared\n')]
    conn.close()
