import datetime
import json
import os

import mock
import pytest
from pyzabbix import ZabbixAPIException

from monplat import actions, cli, events, remediation, templates
from monplat.api.app import create_app

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
RULES_FILE = os.path.join(ROOT, 'config', 'remediation.yml')
TEMPLATE_DIR = os.path.join(ROOT, 'config', 'templates')

NOW = datetime.datetime(2014, 12, 18, 12, 0, 0, tzinfo=events.UTC)

RULES_YAML = """\
rules:
  - name: clear-spool
    trigger_match: "^Spool directory too large"
    min_severity: warning
    script: "MP clear spool"
    command: "rm -f /var/spool/mp-demo/* && echo cleared"
    expect_output: cleared
    cooldown_seconds: 600
"""


def event(status='PROBLEM', severity=2,
          name='Spool directory too large on Zabbix server',
          host='Zabbix server'):
    return events.Event(eventid=4321, status=status, host=host,
                        trigger_id=13600, trigger_name=name,
                        severity=severity, item_value='10485760',
                        event_time=NOW)


@pytest.fixture
def rules(tmpdir):
    path = tmpdir.join('remediation.yml')
    path.write(RULES_YAML)
    return remediation.load_rules(str(path))


def cursor_conn(fetchone=None):
    conn = mock.MagicMock(name='conn')
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = fetchone
    return conn, cursor


def sql_calls(cursor, prefix):
    return [c[0] for c in cursor.execute.call_args_list
            if c[0][0].lstrip().upper().startswith(prefix)]


def zabbix(execute=None, scripts=None, hosts=None):
    zapi = mock.MagicMock(name='zapi')
    zapi.script.get.return_value = ([{'scriptid': '5',
                                      'name': 'MP clear spool'}]
                                    if scripts is None else scripts)
    zapi.host.get.return_value = ([{'hostid': '10084',
                                    'host': 'Zabbix server'}]
                                  if hosts is None else hosts)
    if isinstance(execute, Exception):
        zapi.script.execute.side_effect = execute
    else:
        zapi.script.execute.return_value = (
            execute or {'response': 'success', 'value': 'cleared\n'})
    return zapi


# --- rules -------------------------------------------------------------------

def test_shipped_rules_describe_clear_spool():
    loaded = remediation.load_rules(RULES_FILE)
    assert [r.name for r in loaded] == ['clear-spool']
    rule = loaded[0]
    assert rule.script == 'MP clear spool'
    assert rule.command == 'rm -f /var/spool/mp-demo/* && echo cleared'
    assert rule.expect_output == 'cleared'
    assert rule.min_severity == templates.SEVERITIES['warning']
    assert rule.cooldown_seconds == 600


@pytest.mark.parametrize('content, message', [
    ('rules: []\n', 'no rules'),
    (RULES_YAML.replace('    script: "MP clear spool"\n', ''), 'script'),
    (RULES_YAML.replace('min_severity: warning', 'min_severity: loud'),
     'loud'),
    (RULES_YAML.replace('"^Spool directory too large"', '"(unclosed"'),
     'trigger_match'),
])
def test_load_rules_rejects_bad_rules(tmpdir, content, message):
    path = tmpdir.join('bad.yml')
    path.write(content)
    with pytest.raises(templates.SpecError) as exc:
        remediation.load_rules(str(path))
    assert message in str(exc.value)


def test_rule_matches_trigger_name_pattern_and_minimum_severity(rules):
    assert remediation.match(rules, event()).name == 'clear-spool'
    assert remediation.match(rules, event(severity=5)).name == 'clear-spool'


def test_rule_ignores_lower_severity_and_other_triggers(rules):
    assert remediation.match(rules, event(severity=1)) is None
    assert remediation.match(rules, event(name='High CPU on x')) is None
    # The pattern is anchored at the start of the trigger name.
    assert remediation.match(
        rules, event(name='Old Spool directory too large on x')) is None


def test_ok_events_never_match(rules):
    assert remediation.match(rules, event(status='OK')) is None


def test_ok_events_never_remediate(rules):
    zapi = zabbix()
    conn, cursor = cursor_conn()
    assert remediation.remediate(zapi, conn, event(status='OK'), rules,
                                 event_id=7, now=NOW) is None
    assert zapi.mock_calls == []
    assert cursor.execute.call_args_list == []


# --- cooldown ----------------------------------------------------------------

def test_in_cooldown_queries_recent_runs_for_host_and_rule(rules):
    conn, cursor = cursor_conn(fetchone=(1,))
    assert remediation.in_cooldown(conn, 'Zabbix server', rules[0], NOW)
    sql, params = cursor.execute.call_args[0]
    assert 'FROM remediations' in sql
    assert params == ('Zabbix server', 'clear-spool',
                      NOW - datetime.timedelta(seconds=600))


def test_not_in_cooldown_without_recent_run(rules):
    conn, _ = cursor_conn(fetchone=None)
    assert not remediation.in_cooldown(conn, 'Zabbix server', rules[0], NOW)


def test_cooldown_blocks_a_second_run(rules):
    zapi = zabbix()
    conn, cursor = cursor_conn(fetchone=(1,))
    result = remediation.remediate(zapi, conn, event(), rules, event_id=7,
                                   now=NOW)
    assert result['rule'] == 'clear-spool'
    assert result['ran'] is False
    assert 'cooldown' in result['output']
    assert not zapi.script.execute.called
    assert sql_calls(cursor, 'INSERT') == []


# --- running the script ------------------------------------------------------

def test_successful_run_is_logged_with_ok_true(rules):
    zapi = zabbix()
    conn, cursor = cursor_conn(fetchone=None)
    result = remediation.remediate(zapi, conn, event(), rules, event_id=7,
                                   now=NOW)
    zapi.script.execute.assert_called_once_with(scriptid='5',
                                                hostid='10084')
    assert result == {'rule': 'clear-spool', 'script': 'MP clear spool',
                      'host': 'Zabbix server', 'ran': True, 'ok': True,
                      'output': 'cleared\n'}
    inserts = sql_calls(cursor, 'INSERT')
    assert len(inserts) == 1
    sql, params = inserts[0]
    assert 'INTO remediations' in sql
    assert params == (7, 'clear-spool', 'Zabbix server', 'MP clear spool',
                      True, 'cleared\n')
    assert conn.commit.called


@pytest.mark.parametrize('execute, expected_output', [
    (ZabbixAPIException('Cannot connect to [127.0.0.1:10050]'),
     'Cannot connect'),
    ({'response': 'failed', 'value': 'Remote commands are not enabled.'},
     'Remote commands are not enabled.'),
    ({'response': 'success',
      'value': "rm: cannot remove '/var/spool/mp-demo/blob': "
               "Operation not permitted"},
     'Operation not permitted'),
])
def test_failed_run_is_logged_with_ok_false(rules, execute, expected_output):
    zapi = zabbix(execute=execute)
    conn, cursor = cursor_conn(fetchone=None)
    result = remediation.remediate(zapi, conn, event(), rules, event_id=7,
                                   now=NOW)
    assert result['ran'] is True
    assert result['ok'] is False
    assert expected_output in result['output']
    sql, params = sql_calls(cursor, 'INSERT')[0]
    assert params[4] is False
    assert expected_output in params[5]


def test_missing_script_or_host_is_logged_with_ok_false(rules):
    for zapi, text in ((zabbix(scripts=[]), 'MP clear spool'),
                       (zabbix(hosts=[]), 'Zabbix server')):
        conn, cursor = cursor_conn(fetchone=None)
        result = remediation.remediate(zapi, conn, event(), rules,
                                       event_id=7, now=NOW)
        assert result['ok'] is False
        assert text in result['output']
        assert not zapi.script.execute.called
        assert sql_calls(cursor, 'INSERT')[0][1][4] is False


def test_host_is_found_by_visible_name_when_technical_name_differs(rules):
    zapi = zabbix()
    zapi.host.get.side_effect = lambda filter, **kw: (
        [{'hostid': '10200', 'host': 'srv-01'}]
        if filter.get('name') == 'Server one' else [])
    conn, _ = cursor_conn(fetchone=None)
    result = remediation.remediate(zapi, conn, event(host='Server one'),
                                   rules, event_id=7, now=NOW)
    assert result['ok'] is True
    zapi.script.execute.assert_called_once_with(scriptid='5',
                                                hostid='10200')


# --- provisioning the global script -------------------------------------------

def test_plan_scripts_creates_missing_global_script(rules):
    zapi = mock.MagicMock()
    zapi.script.get.return_value = []
    changes = actions.plan_scripts(zapi, rules)
    assert [(c.action, c.obj, c.name, c.method) for c in changes] == [
        ('create', 'script', 'MP clear spool', 'script.create')]
    params = changes[0].params
    assert params['command'] == 'rm -f /var/spool/mp-demo/* && echo cleared'
    assert params['execute_on'] == actions.EXECUTE_ON_AGENT == 0
    assert params['type'] == actions.SCRIPT_TYPE_CUSTOM == 0


def test_plan_scripts_updates_a_changed_command_and_keeps_a_matching_one(
        rules):
    zapi = mock.MagicMock()
    zapi.script.get.return_value = [{
        'scriptid': '5', 'name': 'MP clear spool', 'type': '0',
        'execute_on': '1', 'command': 'rm -f /var/spool/mp-demo/*'}]
    changes = actions.plan_scripts(zapi, rules)
    assert [(c.action, c.method) for c in changes] == [
        ('update', 'script.update')]
    assert changes[0].params == {
        'scriptid': '5', 'execute_on': 0,
        'command': 'rm -f /var/spool/mp-demo/* && echo cleared'}

    zapi.script.get.return_value = [{
        'scriptid': '5', 'name': 'MP clear spool', 'type': '0',
        'execute_on': '0',
        'command': 'rm -f /var/spool/mp-demo/* && echo cleared'}]
    assert [c.action for c in actions.plan_scripts(zapi, rules)] == [
        'unchanged']


def test_applying_a_script_plan_records_the_new_id(rules):
    zapi = mock.MagicMock()
    zapi.script.get.return_value = []
    zapi.script.create.return_value = {'scriptids': ['9']}
    ids = templates.apply(zapi, actions.plan_scripts(zapi, rules))
    assert ids[('script', 'MP clear spool')] == '9'


def test_provision_runs_the_script_step(tmpdir, capsys):
    rules_file = tmpdir.join('remediation.yml')
    rules_file.write(RULES_YAML)
    zapi = mock.MagicMock()
    for obj in ('hostgroup', 'template', 'application', 'item', 'trigger',
                'host', 'mediatype', 'action', 'script'):
        getattr(zapi, obj).get.return_value = []
    zapi.user.get.return_value = [{'userid': '1', 'alias': 'Admin',
                                   'medias': []}]
    with mock.patch('monplat.zabbix.api.connect', return_value=zapi):
        code = cli.main(['provision', '--dry-run',
                         '--remediation', str(rules_file)])
    assert code == 0
    out, _ = capsys.readouterr()
    assert 'script' in out and 'MP clear spool' in out


def test_provision_exits_2_on_invalid_rules(tmpdir, capsys):
    rules_file = tmpdir.join('remediation.yml')
    rules_file.write('rules: []\n')
    with mock.patch('monplat.zabbix.api.connect') as connect:
        code = cli.main(['provision', '--remediation', str(rules_file)])
    assert code == 2
    assert not connect.called


# --- the spool template --------------------------------------------------------

def test_spool_template_links_to_the_stock_zabbix_server_host():
    spec = [s for s in templates.load_specs(TEMPLATE_DIR)
            if s['template'] == 'Template MP Spool'][0]
    templates.validate(spec)
    assert spec['items'] == [{
        'key': 'vfs.file.size[/var/spool/mp-demo/blob]',
        'name': 'Demo spool blob size', 'type': 'agent',
        'value_type': 'unsigned', 'units': 'B', 'application': 'Spool',
        'delay': 30}]
    assert spec['triggers'] == [{
        'name': 'Spool directory too large on {HOST.NAME}',
        'expression': '{Template MP Spool:vfs.file.size'
                      '[/var/spool/mp-demo/blob].last()}>5242880',
        'severity': 'warning'}]
    assert [h['host'] for h in spec['hosts']] == ['Zabbix server']
    rule = remediation.load_rules(RULES_FILE)[0]
    assert remediation.match(
        [rule], event(name='Spool directory too large on Zabbix server',
                      severity=templates.SEVERITIES['warning'])) is rule


# --- POST /api/v1/events -------------------------------------------------------

CFG = {'database': {'zabbix_dsn': 'dbname=zabbix',
                    'monplat_dsn': 'dbname=monplat'}}

BODY = ('eventid=4321\n'
        'status=PROBLEM\n'
        'host=Zabbix server\n'
        'trigger_id=13600\n'
        'trigger_name=Spool directory too large on Zabbix server\n'
        'severity=2\n'
        'value=10485760\n'
        'time=2014.12.18 12:00:00\n')


@pytest.yield_fixture
def http():
    with mock.patch('monplat.api.app.db.connect') as connect, \
            mock.patch('monplat.api.app.events.store', return_value=7), \
            mock.patch('monplat.api.app.events.claim_notification',
                       return_value=True), \
            mock.patch('monplat.api.app.notify.push', return_value=True):
        connect.return_value = mock.MagicMock(name='conn')
        app = create_app(CFG)
        app.testing = True
        yield app.test_client()


def test_post_of_matching_problem_runs_remediation(http):
    outcome = {'rule': 'clear-spool', 'script': 'MP clear spool',
               'host': 'Zabbix server', 'ran': True, 'ok': True,
               'output': 'cleared\n'}
    zapi = mock.MagicMock(name='zapi')
    with mock.patch('monplat.api.app.zabbix_api.connect',
                    return_value=zapi) as connect, \
            mock.patch('monplat.api.app.remediation.remediate',
                       return_value=outcome) as remediate:
        response = http.post('/api/v1/events',
                             data={'subject': 'PROBLEM', 'body': BODY})
    assert response.status_code == 201
    assert json.loads(response.data)['remediation'] == outcome
    assert connect.called
    args, kwargs = remediate.call_args
    assert args[0] is zapi
    assert args[2].trigger_name == \
        'Spool directory too large on Zabbix server'
    assert [r.name for r in args[3]] == ['clear-spool']
    assert kwargs['event_id'] == 7


def test_post_of_unmatched_event_does_not_contact_zabbix(http):
    with mock.patch('monplat.api.app.zabbix_api.connect') as connect, \
            mock.patch('monplat.api.app.remediation.remediate') as remediate:
        response = http.post('/api/v1/events', data={
            'subject': 'OK', 'body': BODY.replace('status=PROBLEM',
                                                  'status=OK')})
    assert response.status_code == 201
    assert json.loads(response.data)['remediation'] is None
    assert not connect.called
    assert not remediate.called


def test_unreachable_zabbix_is_recorded_as_failed_remediation(http):
    from monplat.zabbix import api
    with mock.patch('monplat.api.app.zabbix_api.connect',
                    side_effect=api.ZabbixUnavailable('down')), \
            mock.patch('monplat.api.app.remediation.in_cooldown',
                       return_value=False), \
            mock.patch('monplat.api.app.remediation.record') as record:
        response = http.post('/api/v1/events',
                             data={'subject': 'PROBLEM', 'body': BODY})
    assert response.status_code == 201
    result = json.loads(response.data)['remediation']
    assert result['ok'] is False
    assert 'down' in result['output']
    assert record.called
