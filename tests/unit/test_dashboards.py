import collections
import json
import os

import pytest

from monplat import cli, dashboards, templates
from monplat.templates import Spec

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
COMMITTED = os.path.join(ROOT, 'grafana', 'dashboards')

SPEC = Spec({
    'template': 'Template MP Linux',
    'group': 'MP Templates',
    'applications': ['CPU', 'Filesystem'],
    'items': [
        {'key': 'mp.cpu.util', 'name': 'CPU utilisation', 'type': 'trapper',
         'value_type': 'float', 'units': '%', 'application': 'CPU'},
        {'key': 'mp.load1', 'name': 'Load average (1 min)',
         'type': 'trapper', 'value_type': 'float', 'units': '',
         'application': 'CPU'},
        {'key': 'mp.fs.pused[/]', 'name': 'Root FS used', 'type': 'trapper',
         'value_type': 'float', 'units': '%', 'application': 'Filesystem'},
    ],
    'triggers': [],
    'hosts': [{'host': 'mp-collector', 'groups': ['MP Servers'],
               'interfaces': []}],
}, path='config/templates/mp-linux.yml')


def _panels(dashboard):
    return [p for row in dashboard['rows'] for p in row['panels']]


def test_build_uses_schema_version_6():
    assert dashboards.build(SPEC)['version'] == 6


def test_build_titles_the_dashboard_after_the_template():
    assert dashboards.build(SPEC)['title'] == 'Template MP Linux'


def test_build_has_one_row_per_application_in_spec_order():
    rows = dashboards.build(SPEC)['rows']
    assert [r['title'] for r in rows] == ['CPU', 'Filesystem']


def test_build_has_one_graph_panel_per_numeric_item():
    rows = dashboards.build(SPEC)['rows']
    assert [p['title'] for p in rows[0]['panels']] == [
        'CPU utilisation', 'Load average (1 min)']
    assert [p['title'] for p in rows[1]['panels']] == ['Root FS used']
    assert all(p['type'] == 'graph' for p in _panels(dashboards.build(SPEC)))


def test_panel_targets_are_the_graphite_paths_of_each_host():
    panels = _panels(dashboards.build(SPEC))
    assert [[t['target'] for t in p['targets']] for p in panels] == [
        ['zabbix.mp-collector.mp_cpu_util'],
        ['zabbix.mp-collector.mp_load1'],
        ['zabbix.mp-collector.mp_fs_pused_'],
    ]


def test_panel_ids_are_unique():
    ids = [p['id'] for p in _panels(dashboards.build(SPEC))]
    assert len(ids) == len(set(ids)) == 3


def test_percent_units_use_the_percent_axis_format():
    panels = _panels(dashboards.build(SPEC))
    assert panels[0]['y_formats'][0] == 'percent'
    assert panels[1]['y_formats'][0] == 'short'


def test_byte_units_use_the_bytes_axis_format():
    spec = Spec(dict(SPEC, items=[
        {'key': 'mp.net.in.bytes', 'name': 'In', 'type': 'trapper',
         'value_type': 'unsigned', 'units': 'B', 'application': 'CPU'}]))
    assert _panels(dashboards.build(spec))[0]['y_formats'][0] == 'bytes'


def test_items_without_an_application_go_to_an_other_row():
    spec = Spec(dict(SPEC, items=[
        {'key': 'k', 'name': 'Loose', 'type': 'trapper',
         'value_type': 'float'}]))
    rows = dashboards.build(spec)['rows']
    assert [r['title'] for r in rows] == ['Other']


def test_applications_without_items_get_no_row():
    spec = Spec(dict(SPEC, applications=['CPU', 'Empty', 'Filesystem']))
    assert 'Empty' not in [r['title'] for r in
                           dashboards.build(spec)['rows']]


def test_dumps_is_deterministic_with_sorted_keys():
    text = dashboards.dumps(dashboards.build(SPEC))
    assert text == dashboards.dumps(dashboards.build(SPEC))
    assert text.endswith('\n')
    reloaded = json.loads(text,
                          object_pairs_hook=collections.OrderedDict)
    assert list(reloaded) == sorted(reloaded)


def test_filename_follows_the_spec_file_name():
    assert dashboards.filename(SPEC) == 'mp-linux.json'


def test_write_creates_one_file_per_spec(tmpdir):
    written = dashboards.write([SPEC], str(tmpdir))
    assert written == [str(tmpdir.join('mp-linux.json'))]
    data = json.loads(tmpdir.join('mp-linux.json').read())
    assert data['title'] == 'Template MP Linux'


@pytest.mark.parametrize('name', ['mp-linux.json', 'mp-snmp.json',
                                  'mp-spool.json'])
def test_committed_dashboards_match_regeneration(tmpdir, name):
    specs = templates.load_specs(os.path.join(ROOT, 'config', 'templates'))
    dashboards.write(specs, str(tmpdir))
    with open(os.path.join(COMMITTED, name)) as handle:
        committed = handle.read()
    assert tmpdir.join(name).read() == committed


def test_cli_dashboards_writes_files(tmpdir, capsys):
    code = cli.main(['dashboards',
                     '--templates', os.path.join(ROOT, 'config', 'templates'),
                     '--out', str(tmpdir)])
    assert code == 0
    assert sorted(os.listdir(str(tmpdir))) == ['mp-linux.json',
                                               'mp-snmp.json',
                                               'mp-spool.json']
    out, _ = capsys.readouterr()
    assert 'mp-linux.json' in out


def test_cli_dashboards_rejects_an_invalid_spec(tmpdir, capsys):
    tmpdir.join('bad.yml').write('template: X\n')
    code = cli.main(['dashboards', '--templates', str(tmpdir),
                     '--out', str(tmpdir.join('out'))])
    assert code == 2
