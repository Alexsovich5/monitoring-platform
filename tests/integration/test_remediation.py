"""A large file in the demo spool fires "Spool directory too large", the
API runs the "MP clear spool" global script on the Zabbix server's agent,
the run is recorded in ``monplat.remediations`` and the trigger recovers.

``/var/spool/mp-demo`` is a named volume mounted in both the ``zabbix``
container (where the agent polls and deletes) and this test runner."""
import os

import pytest
import requests

from monplat import db

from .conftest import STOCK_HOST, wait_for

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


def _remediations(cfg, ok):
    conn = db.connect(cfg, 'monplat')
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT r.rule, r.script_name, r.output, e.trigger_name'
                       ' FROM remediations r JOIN events e'
                       ' ON e.id = r.event_id'
                       ' WHERE r.host = %s AND r.ok = %s'
                       ' ORDER BY r.id', (STOCK_HOST, ok))
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
    assert script['command'] == ('rm -f /var/spool/mp-demo/* '
                                 '&& echo cleared')


def test_spool_template_is_linked_to_the_zabbix_server_host(zapi):
    hosts = zapi.host.get(filter={'host': STOCK_HOST},
                          selectParentTemplates=['host'], output=['host'])
    linked = [t['host'] for t in hosts[0]['parentTemplates']]
    assert TEMPLATE in linked
    # Linked with host.massadd, so the stock template stays linked too.
    assert 'Template OS Linux' in linked


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
