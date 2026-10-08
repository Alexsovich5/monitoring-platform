import pytest
from pysnmp.entity.rfc3413.oneliner import cmdgen

from .conftest import wait_for

pytestmark = pytest.mark.integration

SNMP_HOST = 'mp-switch-sim'
SYS_UPTIME = '1.3.6.1.2.1.1.3.0'


def test_snmpsim_answers_sysuptime_get():
    error_indication, error_status, _, var_binds = \
        cmdgen.CommandGenerator().getCmd(
            cmdgen.CommunityData('public'),
            cmdgen.UdpTransportTarget(('snmpsim', 1161)),
            SYS_UPTIME)
    assert not error_indication, error_indication
    assert not error_status, error_status
    assert len(var_binds) == 1
    name, value = var_binds[0]
    assert str(name) == SYS_UPTIME
    assert int(value) > 0


def test_switch_sim_is_linked_to_snmp_template(zapi):
    hosts = zapi.host.get(filter={'host': SNMP_HOST}, output=['hostid'],
                          selectParentTemplates=['host'],
                          selectInterfaces=['type', 'dns', 'port'])
    assert len(hosts) == 1
    assert 'Template MP SNMP Device' in [
        t['host'] for t in hosts[0]['parentTemplates']]
    assert [(i['type'], i['dns'], i['port'])
            for i in hosts[0]['interfaces']] == [('2', 'snmpsim', '1161')]


def test_zabbix_polls_sysuptime_from_snmpsim(zapi):
    def lastvalue():
        items = zapi.item.get(host=SNMP_HOST, filter={'key_': 'sysUpTime'},
                              output=['itemid', 'lastvalue', 'lastclock',
                                      'error', 'state'])
        assert items, 'sysUpTime item missing on %s' % SNMP_HOST
        # Before the first poll Zabbix reports lastclock and lastvalue "0".
        return items[0]['lastclock'] != '0' and items[0]

    item = wait_for(lastvalue, timeout=120, interval=5,
                    message='sysUpTime lastvalue on %s' % SNMP_HOST)
    assert int(item['lastvalue']) > 0
