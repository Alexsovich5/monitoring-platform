import copy
import os

import mock
import pytest

from monplat import cli, templates

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
TEMPLATE_DIR = os.path.join(ROOT, 'config', 'templates')

SPEC = {
    'template': 'Template T',
    'group': 'T Templates',
    'applications': ['CPU', 'Filesystem'],
    'items': [
        {'key': 'mp.cpu.util', 'name': 'CPU utilisation', 'type': 'trapper',
         'value_type': 'float', 'units': '%', 'application': 'CPU'},
        {'key': 'mp.fs.pused[/]', 'name': 'Root FS used', 'type': 'trapper',
         'value_type': 'float', 'units': '%', 'application': 'Filesystem'},
    ],
    'triggers': [
        {'name': 'High CPU on {HOST.NAME}',
         'expression': '{Template T:mp.cpu.util.avg(5m)}>90',
         'severity': 'high'},
        {'name': 'FS full on {HOST.NAME}',
         'expression': '{Template T:mp.fs.pused[/].last()}>90',
         'severity': 'average'},
    ],
    'hosts': [{'host': 'h1', 'groups': ['T Servers'], 'interfaces': []}],
}

WRITE_METHODS = ('create', 'update', 'delete', 'massadd', 'massupdate',
                 'massremove')


def spec(**changes):
    s = copy.deepcopy(SPEC)
    s.update(changes)
    return s


def write_calls(zapi):
    return [c for c in zapi.mock_calls
            if c[0].split('.')[-1] in WRITE_METHODS]


# --- validation ------------------------------------------------------------

def test_valid_spec_passes():
    templates.validate(spec())


def test_shipped_template_specs_are_valid():
    specs = templates.load_specs(TEMPLATE_DIR)
    names = [s['template'] for s in specs]
    assert 'Template MP Linux' in names
    for s in specs:
        templates.validate(s)


def test_shipped_linux_template_covers_collector_and_forecast_keys():
    s = [x for x in templates.load_specs(TEMPLATE_DIR)
         if x['template'] == 'Template MP Linux'][0]
    keys = set(i['key'] for i in s['items'])
    assert set(['mp.cpu.util', 'mp.load1', 'mp.mem.pused', 'mp.fs.pused[/]',
                'mp.net.in.bytes', 'mp.net.out.bytes',
                'mp.forecast.hours_left[/]']) <= keys
    assert all(i['type'] == 'trapper' for i in s['items'])
    assert [h['host'] for h in s['hosts']] == ['mp-collector']
    assert s['hosts'][0]['interfaces'] == []
    assert len(s['triggers']) == 4


def test_rejects_unknown_item_type():
    s = spec()
    s['items'][0]['type'] = 'telnet'
    with pytest.raises(templates.SpecError) as exc:
        templates.validate(s)
    assert 'telnet' in str(exc.value)


def test_rejects_item_without_key():
    s = spec()
    del s['items'][1]['key']
    with pytest.raises(templates.SpecError) as exc:
        templates.validate(s)
    assert 'key' in str(exc.value)


def test_rejects_spec_without_template_name():
    s = spec()
    del s['template']
    with pytest.raises(templates.SpecError):
        templates.validate(s)


def test_rejects_snmp_item_without_oid():
    s = spec()
    s['items'].append({'key': 'sysUpTime', 'name': 'Uptime',
                       'type': 'snmpv2', 'value_type': 'unsigned',
                       'snmp_community': 'public', 'application': 'CPU'})
    with pytest.raises(templates.SpecError) as exc:
        templates.validate(s)
    assert 'snmp_oid' in str(exc.value)


def test_rejects_trigger_referencing_undefined_key():
    s = spec()
    s['triggers'][0]['expression'] = '{Template T:mp.cpu.idle.avg(5m)}<10'
    with pytest.raises(templates.SpecError) as exc:
        templates.validate(s)
    assert 'mp.cpu.idle' in str(exc.value)


def test_rejects_trigger_referencing_other_host():
    s = spec()
    s['triggers'][0]['expression'] = '{Other:mp.cpu.util.avg(5m)}>90'
    with pytest.raises(templates.SpecError):
        templates.validate(s)


def test_rejects_unknown_severity_and_application():
    s = spec()
    s['triggers'][0]['severity'] = 'urgent'
    with pytest.raises(templates.SpecError):
        templates.validate(s)
    s = spec()
    s['items'][0]['application'] = 'Disk'
    with pytest.raises(templates.SpecError):
        templates.validate(s)


def test_load_specs_reads_yml_files_in_name_order(tmpdir):
    tmpdir.join('b.yml').write('template: B\n')
    tmpdir.join('a.yml').write('template: A\n')
    tmpdir.join('notes.txt').write('ignored')
    specs = templates.load_specs(str(tmpdir))
    assert [s['template'] for s in specs] == ['A', 'B']
    assert specs[0].path.endswith('a.yml')


def test_load_specs_rejects_non_mapping(tmpdir):
    tmpdir.join('a.yml').write('- just\n- a list\n')
    with pytest.raises(templates.SpecError):
        templates.load_specs(str(tmpdir))


# --- planning against a mocked API ----------------------------------------

def empty_zabbix():
    zapi = mock.MagicMock()
    for obj in ('hostgroup', 'template', 'application', 'item', 'trigger',
                'host', 'mediatype', 'action', 'script'):
        getattr(zapi, obj).get.return_value = []
    # Read by the alert media/action step of "mpctl provision".
    zapi.user.get.return_value = [{'userid': '1', 'alias': 'Admin',
                                   'medias': []}]
    return zapi


def provisioned_zabbix(linked=True):
    """A mocked API in which SPEC has already been applied."""
    zapi = mock.MagicMock()

    def hostgroup_get(filter, **kw):
        ids = {'T Templates': '10', 'T Servers': '11'}
        name = filter['name']
        return [{'groupid': ids[name], 'name': name}] if name in ids else []

    zapi.hostgroup.get.side_effect = hostgroup_get
    zapi.template.get.return_value = [
        {'templateid': '100', 'host': 'Template T'}]
    zapi.application.get.return_value = [
        {'applicationid': '201', 'name': 'CPU'},
        {'applicationid': '202', 'name': 'Filesystem'}]
    zapi.item.get.return_value = [
        {'itemid': '301', 'key_': 'mp.cpu.util', 'name': 'CPU utilisation',
         'type': '2', 'value_type': '0', 'units': '%',
         'applications': [{'applicationid': '201', 'name': 'CPU'}]},
        {'itemid': '302', 'key_': 'mp.fs.pused[/]', 'name': 'Root FS used',
         'type': '2', 'value_type': '0', 'units': '%',
         'applications': [{'applicationid': '202', 'name': 'Filesystem'}]}]
    zapi.trigger.get.return_value = [
        {'triggerid': '401', 'description': 'High CPU on {HOST.NAME}',
         'expression': '{Template T:mp.cpu.util.avg(5m)}>90',
         'priority': '4'},
        {'triggerid': '402', 'description': 'FS full on {HOST.NAME}',
         'expression': '{Template T:mp.fs.pused[/].last()}>90',
         'priority': '3'}]
    parents = [{'templateid': '100', 'host': 'Template T'}] if linked else []
    zapi.host.get.return_value = [
        {'hostid': '501', 'host': 'h1',
         'groups': [{'groupid': '11', 'name': 'T Servers'}],
         'parentTemplates': parents}]
    return zapi


def actions(changes):
    return [(c.action, c.obj, c.name) for c in changes]


def test_plan_on_empty_zabbix_creates_everything():
    zapi = empty_zabbix()
    changes = templates.plan(zapi, spec())
    assert actions(changes) == [
        ('create', 'hostgroup', 'T Templates'),
        ('create', 'hostgroup', 'T Servers'),
        ('create', 'template', 'Template T'),
        ('create', 'application', 'CPU'),
        ('create', 'application', 'Filesystem'),
        ('create', 'item', 'mp.cpu.util'),
        ('create', 'item', 'mp.fs.pused[/]'),
        ('create', 'trigger', 'High CPU on {HOST.NAME}'),
        ('create', 'trigger', 'FS full on {HOST.NAME}'),
        ('create', 'host', 'h1'),
        ('create', 'link', 'h1 -> Template T'),
    ]
    assert write_calls(zapi) == []


def test_plan_on_provisioned_zabbix_is_unchanged():
    changes = templates.plan(provisioned_zabbix(), spec())
    assert set(c.action for c in changes) == set(['unchanged'])
    assert len(changes) == 11


def test_plan_updates_changed_trigger_expression_only():
    s = spec()
    s['triggers'][0]['expression'] = '{Template T:mp.cpu.util.avg(5m)}>80'
    changes = templates.plan(provisioned_zabbix(), s)
    updated = [c for c in changes if c.action == 'update']
    assert actions(updated) == [('update', 'trigger',
                                 'High CPU on {HOST.NAME}')]
    assert updated[0].params == {
        'triggerid': '401',
        'expression': '{Template T:mp.cpu.util.avg(5m)}>80',
        'priority': 4}
    assert len([c for c in changes if c.action == 'unchanged']) == 10


def test_plan_ignores_whitespace_in_trigger_expression():
    s = spec()
    s['triggers'][0]['expression'] = '{Template T:mp.cpu.util.avg(5m)} > 90'
    changes = templates.plan(provisioned_zabbix(), s)
    assert set(c.action for c in changes) == set(['unchanged'])


def test_plan_updates_changed_item_units():
    s = spec()
    s['items'][0]['units'] = 'pct'
    changes = templates.plan(provisioned_zabbix(), s)
    updated = [c for c in changes if c.action == 'update']
    assert actions(updated) == [('update', 'item', 'mp.cpu.util')]
    assert updated[0].params == {'itemid': '301', 'units': 'pct'}


def test_plan_asks_for_expanded_trigger_expressions():
    zapi = provisioned_zabbix()
    templates.plan(zapi, spec())
    kwargs = zapi.trigger.get.call_args[1]
    assert kwargs.get('expandExpression') in (True, 1)


# --- applying ---------------------------------------------------------------

def test_apply_on_empty_zabbix_passes_created_ids_on():
    zapi = empty_zabbix()
    zapi.hostgroup.create.side_effect = [{'groupids': ['10']},
                                         {'groupids': ['11']}]
    zapi.template.create.return_value = {'templateids': ['100']}
    zapi.application.create.side_effect = [{'applicationids': ['201']},
                                           {'applicationids': ['202']}]
    zapi.item.create.side_effect = [{'itemids': ['301']},
                                    {'itemids': ['302']}]
    zapi.trigger.create.side_effect = [{'triggerids': ['401']},
                                       {'triggerids': ['402']}]
    zapi.host.create.return_value = {'hostids': ['501']}

    templates.apply(zapi, templates.plan(zapi, spec()))

    zapi.template.create.assert_called_once_with(
        host='Template T', groups=[{'groupid': '10'}])
    assert zapi.application.create.call_args_list == [
        mock.call(name='CPU', hostid='100'),
        mock.call(name='Filesystem', hostid='100')]
    first_item = zapi.item.create.call_args_list[0][1]
    assert first_item == {'hostid': '100', 'key_': 'mp.cpu.util',
                          'name': 'CPU utilisation', 'type': 2,
                          'value_type': 0, 'units': '%',
                          'applications': ['201']}
    assert zapi.trigger.create.call_args_list[0] == mock.call(
        description='High CPU on {HOST.NAME}',
        expression='{Template T:mp.cpu.util.avg(5m)}>90', priority=4)
    # Zabbix 2.4 rejects hosts without interfaces, so a trapper-only host
    # gets an agent interface on 127.0.0.1 that none of its items poll.
    zapi.host.create.assert_called_once_with(
        host='h1', groups=[{'groupid': '11'}],
        interfaces=[{'type': 1, 'main': 1, 'useip': 1, 'ip': '127.0.0.1',
                     'dns': '', 'port': '10050'}])
    zapi.host.massadd.assert_called_once_with(
        hosts=[{'hostid': '501'}], templates=[{'templateid': '100'}])


def test_apply_unchanged_plan_makes_no_writes():
    zapi = provisioned_zabbix()
    templates.apply(zapi, templates.plan(zapi, spec()))
    assert write_calls(zapi) == []


def test_linking_existing_host_uses_massadd_not_update():
    zapi = provisioned_zabbix(linked=False)
    changes = templates.plan(zapi, spec())
    assert [actions([c]) for c in changes if c.action != 'unchanged'] == [
        [('create', 'link', 'h1 -> Template T')]]
    templates.apply(zapi, changes)
    zapi.host.massadd.assert_called_once_with(
        hosts=[{'hostid': '501'}], templates=[{'templateid': '100'}])
    for call in zapi.host.update.call_args_list:
        assert 'templates' not in call[1]
    assert not zapi.host.create.called


def test_existing_host_missing_a_group_gets_it_with_massadd():
    zapi = provisioned_zabbix()
    zapi.host.get.return_value[0]['groups'] = [
        {'groupid': '4', 'name': 'Zabbix servers'}]
    changes = templates.plan(zapi, spec())
    templates.apply(zapi, changes)
    zapi.host.massadd.assert_called_once_with(
        hosts=[{'hostid': '501'}], groups=[{'groupid': '11'}])
    assert not zapi.host.update.called


def test_summary_counts_actions():
    zapi = provisioned_zabbix(linked=False)
    assert templates.summary(templates.plan(zapi, spec())) == {
        'create': 1, 'update': 0, 'unchanged': 10}


# --- mpctl provision -------------------------------------------------------

def write_spec_dir(tmpdir, content):
    tmpdir.join('t.yml').write(content)
    return str(tmpdir)


VALID_YAML = """\
template: Template T
group: T Templates
applications: [CPU]
items:
  - {key: mp.cpu.util, name: CPU, type: trapper, value_type: float, application: CPU}
triggers:
  - {name: High CPU, expression: "{Template T:mp.cpu.util.last()}>90", severity: high}
hosts:
  - {host: h1, groups: [T Servers], interfaces: []}
"""


def test_provision_dry_run_makes_no_api_writes(tmpdir, capsys):
    zapi = empty_zabbix()
    with mock.patch('monplat.zabbix.api.connect', return_value=zapi):
        code = cli.main(['provision', '--dry-run',
                         '--templates', write_spec_dir(tmpdir, VALID_YAML)])
    assert code == 0
    assert write_calls(zapi) == []
    out, _ = capsys.readouterr()
    assert 'create' in out and 'Template T' in out
    assert 'MP notify API' in out
    assert 'MP clear spool' in out
    assert 'created: 12; updated: 0; unchanged: 0' in out


def test_provision_applies_and_prints_summary(tmpdir, capsys):
    zapi = empty_zabbix()
    with mock.patch('monplat.zabbix.api.connect', return_value=zapi):
        code = cli.main(['provision',
                         '--templates', write_spec_dir(tmpdir, VALID_YAML)])
    assert code == 0
    assert zapi.template.create.called
    assert zapi.host.massadd.called
    assert zapi.mediatype.create.called
    assert zapi.user.addmedia.called
    assert zapi.action.create.called
    assert zapi.script.create.called
    out, _ = capsys.readouterr()
    assert 'created: 12; updated: 0; unchanged: 0' in out


def test_provision_exits_2_on_invalid_spec(tmpdir, capsys):
    bad = VALID_YAML.replace('type: trapper', 'type: telnet')
    zapi = empty_zabbix()
    with mock.patch('monplat.zabbix.api.connect', return_value=zapi) as conn:
        code = cli.main(['provision',
                         '--templates', write_spec_dir(tmpdir, bad)])
    assert code == 2
    assert not conn.called
    _, err = capsys.readouterr()
    assert 'telnet' in err


def test_provision_exits_1_when_zabbix_unreachable(tmpdir, capsys):
    from monplat.zabbix import api
    with mock.patch('monplat.zabbix.api.connect',
                    side_effect=api.ZabbixUnavailable('down')):
        code = cli.main(['provision',
                         '--templates', write_spec_dir(tmpdir, VALID_YAML)])
    assert code == 1
