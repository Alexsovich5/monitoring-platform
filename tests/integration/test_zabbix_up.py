import psycopg2
import pytest

from .conftest import STOCK_HOST, agent_get, supervisor_processes, wait_for

pytestmark = pytest.mark.integration

DAEMONS = ('zabbix_server', 'zabbix_agentd', 'apache2')


def test_api_reports_version_2_4_3(zapi):
    assert zapi.api_version() == '2.4.3'


def test_stock_zabbix_server_host_exists(zapi):
    hosts = zapi.host.get(filter={'host': STOCK_HOST}, output=['host'])
    assert [h['host'] for h in hosts] == [STOCK_HOST]


def test_fixture_enables_stock_host(zapi):
    hosts = zapi.host.get(filter={'host': STOCK_HOST}, output=['status'])
    assert hosts[0]['status'] == '0'


def _agent_history_rows(dsn):
    conn = psycopg2.connect(dsn)
    try:
        cur = conn.cursor()
        cur.execute("""
            SELECT
              (SELECT count(*) FROM history h
                 JOIN items i ON i.itemid = h.itemid
                 JOIN hosts ho ON ho.hostid = i.hostid
                WHERE ho.host = %(host)s AND i.type = 0)
            + (SELECT count(*) FROM history_uint h
                 JOIN items i ON i.itemid = h.itemid
                 JOIN hosts ho ON ho.hostid = i.hostid
                WHERE ho.host = %(host)s AND i.type = 0)
        """, {'host': STOCK_HOST})
        return cur.fetchone()[0]
    finally:
        conn.close()


def test_server_writes_history_for_its_agent_items(cfg, zapi):
    dsn = cfg['database']['zabbix_dsn']
    rows = wait_for(lambda: _agent_history_rows(dsn), timeout=120,
                    interval=5,
                    message='no agent history rows for %s' % STOCK_HOST)
    assert rows > 0


def _running_for_30s():
    procs = dict((p['name'], p) for p in supervisor_processes())
    if not all(name in procs for name in DAEMONS):
        return False
    for name in DAEMONS:
        p = procs[name]
        if p['statename'] != 'RUNNING' or p['now'] - p['start'] < 30:
            return False
    return procs


def test_supervisor_keeps_all_daemons_running():
    procs = wait_for(_running_for_30s, timeout=150, interval=5,
                     message='zabbix_server, zabbix_agentd and apache2 '
                             'not all RUNNING for 30 s')
    assert sorted(n for n in procs if n in DAEMONS) == sorted(DAEMONS)


@pytest.mark.parametrize('name', ['zabbix_server', 'zabbix_agentd'])
def test_forked_daemon_process_is_alive(name):
    # proc.num[<name>] counts processes whose name is exactly <name>,
    # the same match as "pgrep -x <name>" inside the container.
    count = int(agent_get('proc.num[%s]' % name))
    assert count >= 1
