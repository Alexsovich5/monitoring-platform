"""Generate Grafana 1.x dashboards from the template specs.

Each spec (``config/templates/<name>.yml``) becomes
``grafana/dashboards/<name>.json``: one row per application that has items
and one graph panel per numeric item, plotting the item's Graphite path
(``monplat.api.graphite.metric_path``) on every host the spec declares.
The JSON is written with sorted keys and fixed indentation so that the
committed files match a regeneration byte for byte.
"""
import json
import os

from monplat.api.graphite import metric_path
from monplat.templates import VALUE_TYPES

# The dashboard schema version of Grafana 1.9's dashboardSrv; dashboards at
# this version are loaded without client-side migration.
SCHEMA_VERSION = 6
DEFAULT_DIR = os.path.join('grafana', 'dashboards')
OTHER_ROW = 'Other'
ROW_HEIGHT = '250px'
PANELS_PER_ROW = 3

# Zabbix units -> Grafana y-axis formats.
Y_FORMATS = {'%': 'percent', 'B': 'bytes', 'Bps': 'Bps', 's': 's',
             'ms': 'ms'}


def _y_format(units):
    return Y_FORMATS.get(units or '', 'short')


def _graph(panel_id, item, hosts, span):
    return {
        'id': panel_id,
        'title': item['name'],
        'type': 'graph',
        'span': span,
        'editable': True,
        'datasource': None,
        'renderer': 'flot',
        'x-axis': True,
        'y-axis': True,
        'y_formats': [_y_format(item.get('units')), 'short'],
        'grid': {'leftMax': None, 'leftMin': None, 'rightMax': None,
                 'rightMin': None, 'threshold1': None, 'threshold2': None,
                 'threshold1Color': 'rgba(216, 200, 27, 0.27)',
                 'threshold2Color': 'rgba(234, 112, 112, 0.22)'},
        'lines': True,
        'fill': 1,
        'linewidth': 1,
        'points': False,
        'pointradius': 5,
        'bars': False,
        'stack': False,
        'percentage': False,
        'steppedLine': False,
        'nullPointMode': 'connected',
        'legend': {'show': True, 'values': False, 'min': False,
                   'max': False, 'current': True, 'total': False,
                   'avg': False},
        'tooltip': {'value_type': 'individual', 'shared': False},
        'targets': [{'target': metric_path(host, item['key'])}
                    for host in hosts],
        'aliasColors': {},
        'seriesOverrides': [],
        'links': [],
    }


def _row_titles(spec):
    """Application names in spec order, then ``Other`` for items that
    have no application."""
    titles = list(spec.get('applications') or [])
    if any(not item.get('application') for item in spec.get('items') or []):
        titles.append(OTHER_ROW)
    return titles


def build(spec):
    """Return the Grafana 1.x dashboard for one template spec."""
    hosts = [h['host'] for h in spec.get('hosts') or []]
    numeric = [item for item in spec.get('items') or []
               if item.get('value_type') in VALUE_TYPES]
    rows = []
    panel_id = 0
    for title in _row_titles(spec):
        items = [item for item in numeric
                 if (item.get('application') or OTHER_ROW) == title]
        if not items:
            continue
        span = 12 // min(len(items), PANELS_PER_ROW)
        panels = []
        for item in items:
            panel_id += 1
            panels.append(_graph(panel_id, item, hosts, span))
        rows.append({'title': title, 'height': ROW_HEIGHT,
                     'editable': True, 'collapse': False,
                     'panels': panels})
    return {
        'title': spec['template'],
        'version': SCHEMA_VERSION,
        'tags': ['monplat'],
        'style': 'dark',
        'timezone': 'browser',
        'editable': True,
        'hideAllLegends': False,
        'sharedCrosshair': False,
        'rows': rows,
        'time': {'from': 'now-1h', 'to': 'now'},
        'refresh': '1m',
        'templating': {'enable': False, 'list': []},
        'annotations': {'enable': False, 'list': []},
        'nav': [{'type': 'timepicker', 'collapse': False, 'enable': True,
                 'notice': False, 'status': 'Stable',
                 'time_options': ['5m', '15m', '1h', '6h', '12h', '24h',
                                  '2d', '7d', '30d'],
                 'refresh_intervals': ['30s', '1m', '5m', '15m', '1h']}],
    }


def dumps(dashboard):
    """Serialise ``dashboard`` deterministically (sorted keys, two-space
    indent, trailing newline)."""
    return json.dumps(dashboard, sort_keys=True, indent=2,
                      separators=(',', ': ')) + '\n'


def filename(spec):
    """``config/templates/mp-linux.yml`` -> ``mp-linux.json``."""
    base = os.path.splitext(os.path.basename(spec.path))[0]
    return base + '.json'


def write(specs, outdir=DEFAULT_DIR):
    """Write one dashboard per spec into ``outdir``; return the paths."""
    if not os.path.isdir(outdir):
        os.makedirs(outdir)
    written = []
    for spec in specs:
        path = os.path.join(outdir, filename(spec))
        with open(path, 'w') as handle:
            handle.write(dumps(build(spec)))
        written.append(path)
    return written
