import re
import subprocess

import pytest

from .conftest import LINUX_TEMPLATE, TEST_HOSTS

pytestmark = pytest.mark.integration

_SUMMARY = re.compile(r'created: (\d+); updated: (\d+); unchanged: (\d+)')


def test_second_provision_run_changes_nothing():
    # make integration has already run "mpctl provision" once.
    proc = subprocess.Popen(['mpctl', 'provision'], stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out, err = proc.communicate()
    assert proc.returncode == 0, out + err
    match = _SUMMARY.search(out)
    assert match, out
    created, updated, unchanged = [int(n) for n in match.groups()]
    assert (created, updated) == (0, 0), out
    assert unchanged > 0


def test_template_and_collector_host_exist(zapi):
    found = zapi.template.get(filter={'host': LINUX_TEMPLATE},
                              output=['host'])
    assert [t['host'] for t in found] == [LINUX_TEMPLATE]
    hosts = zapi.host.get(filter={'host': 'mp-collector'}, output=['host'])
    assert [h['host'] for h in hosts] == ['mp-collector']


def test_template_has_trapper_items_and_triggers(zapi):
    tid = zapi.template.get(filter={'host': LINUX_TEMPLATE},
                            output=['templateid'])[0]['templateid']
    items = zapi.item.get(hostids=[tid], output=['key_', 'type'])
    keys = dict((i['key_'], i['type']) for i in items)
    assert keys['mp.cpu.util'] == '2'
    assert keys['mp.forecast.hours_left[/]'] == '2'
    triggers = zapi.trigger.get(hostids=[tid], output=['description'])
    assert len(triggers) == 4


def _parent_templates(zapi, host):
    hosts = zapi.host.get(filter={'host': host}, output=['hostid'],
                          selectParentTemplates=['host'])
    return [t['host'] for t in hosts[0]['parentTemplates']]


def test_collector_host_is_linked_to_linux_template(zapi):
    assert LINUX_TEMPLATE in _parent_templates(zapi, 'mp-collector')


def test_collector_host_inherits_template_items(zapi):
    items = zapi.item.get(host='mp-collector', output=['key_'])
    assert 'mp.fs.pused[/]' in [i['key_'] for i in items]


def test_test_hosts_fixture_links_each_host(zapi, test_hosts):
    assert sorted(test_hosts) == sorted(TEST_HOSTS)
    for name in TEST_HOSTS:
        assert LINUX_TEMPLATE in _parent_templates(zapi, name)
