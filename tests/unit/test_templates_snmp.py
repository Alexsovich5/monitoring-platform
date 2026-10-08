import copy
import os

import mock
import pytest

from monplat import templates

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
TEMPLATE_DIR = os.path.join(ROOT, 'config', 'templates')

SNMP_SPEC = {
    'template': 'Template S',
    'group': 'S Templates',
    'applications': ['System'],
    'items': [
        {'key': 'sysUpTime', 'name': 'Uptime', 'type': 'snmpv2',
         'value_type': 'unsigned', 'units': '', 'application': 'System',
         'snmp_oid': '1.3.6.1.2.1.1.3.0', 'snmp_community': 'public',
         'delay': 30},
    ],
    'triggers': [],
    'hosts': [{'host': 'sw1', 'groups': ['S Devices'],
               'interfaces': [{'type': 'snmp', 'dns': 'snmpsim',
                               'port': 1161}]}],
}


def spec():
    return copy.deepcopy(SNMP_SPEC)


def empty_zabbix():
    zapi = mock.MagicMock()
    for obj in ('hostgroup', 'template', 'application', 'item', 'trigger',
                'host'):
        getattr(zapi, obj).get.return_value = []
    return zapi


def changes_for(changes, obj):
    return [c for c in changes if c.obj == obj]


def test_snmp_spec_with_snmp_interface_is_valid():
    templates.validate(spec())


def test_rejects_unknown_interface_type():
    s = spec()
    s['hosts'][0]['interfaces'][0]['type'] = 'ipmi'
    with pytest.raises(templates.SpecError) as exc:
        templates.validate(s)
    assert 'ipmi' in str(exc.value)


def test_rejects_snmp_interface_without_port():
    s = spec()
    del s['hosts'][0]['interfaces'][0]['port']
    with pytest.raises(templates.SpecError) as exc:
        templates.validate(s)
    assert "missing 'port'" in str(exc.value)


def test_snmp_item_maps_to_type_4_with_oid_and_community():
    changes = templates.plan(empty_zabbix(), spec())
    item = changes_for(changes, 'item')[0]
    assert item.method == 'item.create'
    params = item.params
    assert params['type'] == 4
    assert params['snmp_oid'] == '1.3.6.1.2.1.1.3.0'
    assert params['snmp_community'] == 'public'
    assert params['value_type'] == 3
    assert params['delay'] == 30


def test_trapper_item_carries_no_snmp_fields():
    s = spec()
    s['items'] = [{'key': 't.k', 'name': 'T', 'type': 'trapper',
                   'value_type': 'float'}]
    item = changes_for(templates.plan(empty_zabbix(), s), 'item')[0]
    assert 'snmp_oid' not in item.params
    assert 'snmp_community' not in item.params


def test_snmp_host_interface_maps_to_type_2_on_port_1161():
    changes = templates.plan(empty_zabbix(), spec())
    host = changes_for(changes, 'host')[0]
    assert host.params['interfaces'] == [
        {'type': 2, 'main': 1, 'useip': 0, 'ip': '', 'dns': 'snmpsim',
         'port': '1161'}]


def test_snmp_interface_by_ip_uses_ip():
    s = spec()
    s['hosts'][0]['interfaces'] = [{'type': 'snmp', 'ip': '10.0.0.5',
                                    'port': 161}]
    host = changes_for(templates.plan(empty_zabbix(), s), 'host')[0]
    assert host.params['interfaces'] == [
        {'type': 2, 'main': 1, 'useip': 1, 'ip': '10.0.0.5', 'dns': '',
         'port': '161'}]


def test_plan_reads_snmp_fields_and_is_unchanged_when_provisioned():
    zapi = mock.MagicMock()
    zapi.hostgroup.get.side_effect = lambda filter, **kw: [
        {'groupid': '1', 'name': filter['name']}]
    zapi.template.get.return_value = [{'templateid': '100',
                                       'host': 'Template S'}]
    zapi.application.get.return_value = [{'applicationid': '201',
                                          'name': 'System'}]
    zapi.item.get.return_value = [
        {'itemid': '301', 'key_': 'sysUpTime', 'name': 'Uptime',
         'type': '4', 'value_type': '3', 'units': '', 'delay': '30',
         'snmp_oid': '1.3.6.1.2.1.1.3.0', 'snmp_community': 'public',
         'applications': [{'applicationid': '201', 'name': 'System'}]}]
    zapi.trigger.get.return_value = []
    zapi.host.get.return_value = [
        {'hostid': '501', 'host': 'sw1',
         'groups': [{'groupid': '1', 'name': 'S Devices'}],
         'parentTemplates': [{'templateid': '100', 'host': 'Template S'}]}]

    changes = templates.plan(zapi, spec())

    assert set(c.action for c in changes) == set(['unchanged'])
    output = zapi.item.get.call_args[1]['output']
    assert 'snmp_oid' in output and 'snmp_community' in output


def test_plan_updates_changed_snmp_oid():
    zapi = empty_zabbix()
    zapi.template.get.return_value = [{'templateid': '100',
                                       'host': 'Template S'}]
    zapi.item.get.return_value = [
        {'itemid': '301', 'key_': 'sysUpTime', 'name': 'Uptime',
         'type': '4', 'value_type': '3', 'units': '', 'delay': '30',
         'snmp_oid': '1.3.6.1.2.1.1.3', 'snmp_community': 'public',
         'applications': [{'applicationid': '201', 'name': 'System'}]}]
    item = changes_for(templates.plan(zapi, spec()), 'item')[0]
    assert item.action == 'update'
    assert item.params == {'itemid': '301',
                           'snmp_oid': '1.3.6.1.2.1.1.3.0'}


def test_shipped_snmp_template_polls_switch_sim():
    s = [x for x in templates.load_specs(TEMPLATE_DIR)
         if x['template'] == 'Template MP SNMP Device'][0]
    templates.validate(s)
    items = dict((i['key'], i) for i in s['items'])
    assert items['sysUpTime']['snmp_oid'] == '1.3.6.1.2.1.1.3.0'
    assert all(i['type'] == 'snmpv2' for i in s['items'])
    assert set(['ifInOctets[1]', 'ifOutOctets[1]', 'ifInOctets[2]',
                'ifOutOctets[2]']) <= set(items)
    assert [h['host'] for h in s['hosts']] == ['mp-switch-sim']
    assert s['hosts'][0]['interfaces'] == [
        {'type': 'snmp', 'dns': 'snmpsim', 'port': 1161}]
