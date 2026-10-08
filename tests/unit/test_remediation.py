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
INTEGRATION_RULES_FILE = os.path.join(ROOT, 'tests', 'integration',
                                      'remediation.yml')
TEMPLATE_DIR = os.path.join(ROOT, 'config', 'templates')

NOW = datetime.datetime(2014, 12, 18, 12, 0, 0, tzinfo=events.UTC)
EVENT_CLOCK = '1418904000'

RULES_YAML = """\
rules:
  - name: clear-spool
    enabled: true
    trigger_match: "^Spool directory too large"
    min_severity: warning
    script: "MP clear spool"
    command: "rm -f /var/spool/mp-demo/* && echo cleared"
    expect_output: cleared
    cooldown_seconds: 600
    allow_hosts: ["Zabbix server"]
"""


def event(status='PROBLEM', severity=2,
          name='Spool directory too large on Zabbix server',
          host='Zabbix server', trigger_id=13600):
    return events.Event(eventid=4321, status=status, host=host,
                        trigger_id=trigger_id, trigger_name=name,
                        severity=severity, item_value='10485760',
                        event_time=NOW)


@pytest.fixture
def rules(tmpdir):
    path = tmpdir.join('remediation.yml')
    path.write(RULES_YAML)
    return remediation.load_rules(str(path))


def load(tmpdir, content):
    path = tmpdir.join('rules.yml')
    path.write(content)
    return remediation.load_rules(str(path))


def cursor_conn(fetchone=None):
    conn = mock.MagicMock(name='conn')
    cursor = conn.cursor.return_value
    cursor.fetchone.return_value = fetchone
    return conn, cursor


def sql_calls(cursor, prefix):
    return [c[0] for c in cursor.execute.call_args_list
            if c[0][0].lstrip().upper().startswith(prefix)]


def zabbix(execute=None, scripts=None, zevent=None, trigger=None,
           groups=None):
    """A Zabbix API stand-in that knows PROBLEM event 4321 of trigger
    13600 on "Zabbix server" (hostid 10084)."""
    zapi = mock.MagicMock(name='zapi')
    zapi.script.get.return_value = ([{'scriptid': '5',
                                      'name': 'MP clear spool'}]
                                    if scripts is None else scripts)
    base_event = {'eventid': '4321', 'source': '0', 'object': '0',
                  'objectid': '13600', 'value': '1', 'acknowledged': '0',
                  'clock': EVENT_CLOCK,
                  'hosts': [{'hostid': '10084', 'host': 'Zabbix server'}]}
    if zevent is None:
        zapi.event.get.return_value = [base_event]
    elif zevent == []:
        zapi.event.get.return_value = []
    else:
        zapi.event.get.return_value = [dict(base_event, **zevent)]
    base_trigger = {'triggerid': '13600',
                    'description': 'Spool directory too large on '
                                   'Zabbix server',
                    'priority': '2', 'value': '1',
                    'lastchange': EVENT_CLOCK}
    zapi.trigger.get.return_value = [dict(base_trigger, **(trigger or {}))]
    zapi.host.get.return_value = [{
        'hostid': '10084', 'host': 'Zabbix server',
        'groups': [{'name': g} for g in (groups or ['Zabbix servers'])]}]
    if isinstance(execute, Exception):
        zapi.script.execute.side_effect = execute
    else:
        zapi.script.execute.return_value = (
            execute or {'response': 'success', 'value': 'cleared\n'})
    return zapi


def audit_rows(cursor):
    """The parameters of every INSERT into remediations:
    (event_id, rule, host, script, ok, output, ran)."""
    return [params for sql, params in sql_calls(cursor, 'INSERT')]


# --- rules -------------------------------------------------------------------

def test_shipped_rules_describe_clear_spool_and_are_disabled():
    loaded = remediation.load_rules(RULES_FILE)
    assert [r.name for r in loaded] == ['clear-spool']
    rule = loaded[0]
    assert rule.enabled is False
    assert rule.script == 'MP clear spool'
    assert rule.command == 'rm -f /var/spool/mp-demo/* && echo cleared'
    assert rule.expect_output == 'cleared'
    assert rule.min_severity == templates.SEVERITIES['warning']
    assert rule.cooldown_seconds == 600
    assert rule.allow_hosts == ('Zabbix server',)
    assert remediation.match(loaded, event()) is None


def test_integration_rules_enable_the_same_rule():
    shipped = remediation.load_rules(RULES_FILE)[0]
    enabled = remediation.load_rules(INTEGRATION_RULES_FILE)[0]
    assert enabled.enabled is True
    assert enabled._replace(enabled=False) == shipped


def test_rules_are_disabled_unless_enabled_is_set(tmpdir):
    loaded = load(tmpdir, RULES_YAML.replace('    enabled: true\n', ''))
    assert loaded[0].enabled is False
    assert remediation.match(loaded, event()) is None


def test_rules_file_comes_from_the_config():
    assert remediation.rules_path({}) == remediation.DEFAULT_PATH
    assert remediation.rules_path(
        {'remediation': {'rules_file': '/x/rules.yml'}}) == '/x/rules.yml'


@pytest.mark.parametrize('content, message', [
    ('rules: []\n', 'no rules'),
    (RULES_YAML.replace('    script: "MP clear spool"\n', ''), 'script'),
    (RULES_YAML.replace('min_severity: warning', 'min_severity: loud'),
     'loud'),
    (RULES_YAML.replace('"^Spool directory too large"', '"(unclosed"'),
     'trigger_match'),
    (RULES_YAML.replace('    allow_hosts: ["Zabbix server"]\n', ''),
     'allow_hosts'),
    (RULES_YAML.replace('allow_hosts: ["Zabbix server"]',
                        'allow_hosts: "Zabbix server"'), 'allow_hosts'),
    (RULES_YAML.replace('enabled: true', 'enabled: "yes"'), 'enabled'),
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
    # Refusals are audited too, but only real runs start a cooldown.
    assert 'AND ran' in sql
    assert params == ('Zabbix server', 'clear-spool',
                      NOW - datetime.timedelta(seconds=600))


def test_not_in_cooldown_without_recent_run(rules):
    conn, _ = cursor_conn(fetchone=None)
    assert not remediation.in_cooldown(conn, 'Zabbix server', rules[0], NOW)


def test_second_run_inside_the_window_is_rate_limited_and_audited(rules):
    zapi = zabbix()
    conn, cursor = cursor_conn(fetchone=(1,))
    result = remediation.remediate(zapi, conn, event(), rules, event_id=7,
                                   now=NOW)
    assert result['rule'] == 'clear-spool'
    assert result['ran'] is False
    assert 'rate-limited' in result['output']
    assert not zapi.script.execute.called
    rows = audit_rows(cursor)
    assert len(rows) == 1
    assert rows[0][4] is False and rows[0][6] is False
    assert 'rate-limited' in rows[0][5]


# --- running the script ------------------------------------------------------

def test_verified_problem_runs_once_on_the_zabbix_hostid(rules):
    zapi = zabbix()
    conn, cursor = cursor_conn(fetchone=None)
    result = remediation.remediate(zapi, conn, event(), rules, event_id=7,
                                   now=NOW)
    get_kwargs = zapi.event.get.call_args[1]
    assert get_kwargs['eventids'] == [4321]
    assert get_kwargs['selectHosts'] == ['hostid', 'host']
    assert zapi.trigger.get.call_args[1]['triggerids'] == ['13600']
    assert zapi.trigger.get.call_args[1]['expandDescription'] is True
    zapi.script.execute.assert_called_once_with(scriptid='5',
                                                hostid='10084')
    assert result == {'rule': 'clear-spool', 'script': 'MP clear spool',
                      'host': 'Zabbix server', 'ran': True, 'ok': True,
                      'output': 'cleared\n'}
    rows = audit_rows(cursor)
    assert rows == [(7, 'clear-spool', 'Zabbix server', 'MP clear spool',
                     True, 'cleared\n', True)]
    sql = sql_calls(cursor, 'INSERT')[0][0]
    assert 'INTO remediations' in sql
    assert conn.commit.called


def _refused(zapi, rules, ev=None):
    conn, cursor = cursor_conn(fetchone=None)
    result = remediation.remediate(zapi, conn, ev or event(), rules,
                                   event_id=7, now=NOW)
    assert not zapi.script.execute.called
    assert result['ran'] is False and result['ok'] is False
    assert result['output'].startswith('refused: ')
    rows = audit_rows(cursor)
    assert len(rows) == 1, 'every refusal is audited'
    assert rows[0][4] is False and rows[0][6] is False
    return result


def test_forged_event_without_a_zabbix_problem_runs_nothing(rules):
    result = _refused(zabbix(zevent=[]), rules)
    assert 'not found' in result['output']


@pytest.mark.parametrize('zevent, trigger, text', [
    ({'value': '0'}, None, 'not a PROBLEM'),
    ({'acknowledged': '1'}, None, 'acknowledged'),
    ({'source': '3'}, None, 'not a trigger event'),
    ({'objectid': '99999'}, None, 'trigger'),
    ({'hosts': []}, None, 'exactly one host'),
    (None, {'value': '0'}, 'no longer in PROBLEM'),
    (None, {'lastchange': str(int(EVENT_CLOCK) + 60)}, 'current problem'),
    (None, {'description': 'High CPU on Zabbix server'}, 'does not match'),
    (None, {'priority': '1'}, 'does not match'),
])
def test_event_that_zabbix_does_not_confirm_is_refused(rules, zevent,
                                                       trigger, text):
    result = _refused(zabbix(zevent=zevent, trigger=trigger), rules)
    assert text in result['output']


def test_message_host_differing_from_zabbix_host_is_refused(rules):
    zapi = zabbix(zevent={'hosts': [{'hostid': '10500', 'host': 'db01'}]})
    result = _refused(zapi, rules)
    assert 'db01' in result['output']


def test_host_outside_the_allowlist_is_refused_and_audited(rules):
    zapi = zabbix(zevent={'hosts': [{'hostid': '10500', 'host': 'db01'}]})
    result = _refused(zapi, rules, event(host='db01'))
    assert 'allow' in result['output']
    assert result['host'] == 'db01'


def test_host_group_allowlist_admits_members(tmpdir):
    rules = load(tmpdir, RULES_YAML.replace(
        'allow_hosts: ["Zabbix server"]', 'allow_groups: ["Spool hosts"]'))
    conn, _ = cursor_conn(fetchone=None)
    zapi = zabbix(groups=['Spool hosts'])
    result = remediation.remediate(zapi, conn, event(), rules, event_id=7,
                                   now=NOW)
    assert result['ok'] is True
    assert zapi.host.get.call_args[1]['hostids'] == ['10084']

    _refused(zabbix(groups=['Zabbix servers']), rules)


def test_injected_host_line_in_the_item_value_cannot_change_the_target(
        rules):
    body = ('eventid=4321\nstatus=PROBLEM\nhost=Zabbix server\n'
            'trigger_id=13600\n'
            'trigger_name=Spool directory too large on Zabbix server\n'
            'severity=2\ntime=2014.12.18 12:00:00\n'
            'value=10485760\nhost=db01\n')
    parsed = events.parse_alert('PROBLEM', body)
    assert parsed.host == 'Zabbix server'
    zapi = zabbix()
    conn, _ = cursor_conn(fetchone=None)
    remediation.remediate(zapi, conn, parsed, rules, event_id=7, now=NOW)
    zapi.script.execute.assert_called_once_with(scriptid='5',
                                                hostid='10084')


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
    params = audit_rows(cursor)[0]
    assert params[4] is False and params[6] is True
    assert expected_output in params[5]


def test_missing_or_ambiguous_script_is_logged_with_ok_false(rules):
    two = [{'scriptid': '5', 'name': 'MP clear spool'},
           {'scriptid': '6', 'name': 'MP clear spool'}]
    for scripts in ([], two):
        zapi = zabbix(scripts=scripts)
        conn, cursor = cursor_conn(fetchone=None)
        result = remediation.remediate(zapi, conn, event(), rules,
                                       event_id=7, now=NOW)
        assert result['ok'] is False
        assert 'MP clear spool' in result['output']
        assert not zapi.script.execute.called
        assert audit_rows(cursor)[0][4] is False


def test_zabbix_error_while_verifying_is_refused_not_raised(rules):
    zapi = zabbix()
    zapi.event.get.side_effect = ZabbixAPIException('No permissions')
    result = _refused(zapi, rules)
    assert 'No permissions' in result['output']


def test_disabled_rule_never_contacts_zabbix(tmpdir):
    rules = load(tmpdir, RULES_YAML.replace('enabled: true',
                                            'enabled: false'))
    zapi = zabbix()
    conn, cursor = cursor_conn()
    assert remediation.remediate(zapi, conn, event(), rules, event_id=7,
                                 now=NOW) is None
    assert zapi.mock_calls == []


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
    # Only users with write access to a host may run it from the frontend.
    assert params['host_access'] == actions.HOST_ACCESS_WRITE == 3


def test_plan_scripts_skips_disabled_rules(tmpdir):
    rules = load(tmpdir, RULES_YAML.replace('enabled: true',
                                            'enabled: false'))
    zapi = mock.MagicMock()
    assert actions.plan_scripts(zapi, rules) == []
    assert zapi.mock_calls == []


def test_plan_scripts_updates_a_changed_command_and_keeps_a_matching_one(
        rules):
    zapi = mock.MagicMock()
    zapi.script.get.return_value = [{
        'scriptid': '5', 'name': 'MP clear spool', 'type': '0',
        'execute_on': '1', 'host_access': '2',
        'command': 'rm -f /var/spool/mp-demo/*'}]
    changes = actions.plan_scripts(zapi, rules)
    assert [(c.action, c.method) for c in changes] == [
        ('update', 'script.update')]
    assert changes[0].params == {
        'scriptid': '5', 'execute_on': 0, 'host_access': 3,
        'command': 'rm -f /var/spool/mp-demo/* && echo cleared'}

    zapi.script.get.return_value = [{
        'scriptid': '5', 'name': 'MP clear spool', 'type': '0',
        'execute_on': '0', 'host_access': '3',
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
    rule = remediation.load_rules(INTEGRATION_RULES_FILE)[0]
    assert remediation.match(
        [rule], event(name='Spool directory too large on Zabbix server',
                      severity=templates.SEVERITIES['warning'])) is rule


# --- POST /api/v1/events -------------------------------------------------------

CFG = {'database': {'zabbix_dsn': 'dbname=zabbix',
                    'monplat_dsn': 'dbname=monplat'},
       'remediation': {'rules_file': INTEGRATION_RULES_FILE}}

BODY = ('eventid=4321\n'
        'status=PROBLEM\n'
        'host=Zabbix server\n'
        'trigger_id=13600\n'
        'trigger_name=Spool directory too large on Zabbix server\n'
        'severity=2\n'
        'time=2014.12.18 12:00:00\n'
        'value=10485760\n')


class Poster(object):
    def __init__(self, client, headers):
        self.client = client
        self.headers = headers

    def post(self, *args, **kwargs):
        return self.client.post(*args, headers=self.headers, **kwargs)


@pytest.yield_fixture
def http(intake):
    with mock.patch('monplat.api.app.db.connect') as connect, \
            mock.patch('monplat.api.app.events.store', return_value=7), \
            mock.patch('monplat.api.app.events.claim_notification',
                       return_value=True), \
            mock.patch('monplat.api.app.notify.push', return_value=True):
        connect.return_value = mock.MagicMock(name='conn')
        app = create_app(intake.cfg(CFG))
        app.testing = True
        yield Poster(app.test_client(), intake.headers)


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


def test_unreachable_zabbix_is_recorded_as_refused_remediation(http):
    from monplat.zabbix import api
    with mock.patch('monplat.api.app.zabbix_api.connect',
                    side_effect=api.ZabbixUnavailable('down')), \
            mock.patch('monplat.api.app.remediation.record') as record:
        response = http.post('/api/v1/events',
                             data={'subject': 'PROBLEM', 'body': BODY})
    assert response.status_code == 201
    result = json.loads(response.data)['remediation']
    assert result['ok'] is False
    assert result['ran'] is False
    assert 'down' in result['output']
    assert record.call_args[1]['ran'] is False


def test_api_uses_the_configured_rules_file(http):
    shipped = dict(CFG, remediation={'rules_file': RULES_FILE})
    with mock.patch('monplat.api.app.zabbix_api.connect') as connect, \
            mock.patch('monplat.api.app.db.connect'), \
            mock.patch('monplat.api.app.events.store', return_value=7), \
            mock.patch('monplat.api.app.events.claim_notification',
                       return_value=False):
        app = create_app(dict(shipped, api=http.client.application
                              .config['MONPLAT']['api']))
        response = app.test_client().post(
            '/api/v1/events', headers=http.headers,
            data={'subject': 'PROBLEM', 'body': BODY})
    assert response.status_code == 201
    # The shipped rule is disabled, so Zabbix is never contacted.
    assert json.loads(response.data)['remediation'] is None
    assert not connect.called
