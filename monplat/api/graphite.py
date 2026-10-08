"""The subset of the Graphite HTTP API that Grafana's Graphite datasource
needs: ``/metrics/find`` and JSON ``/render``, served from Zabbix history.

Every numeric item of a monitored host is a leaf at
``zabbix.<host>.<key>``.  In host and key, characters outside
``[A-Za-z0-9_-]`` become ``_`` and runs of ``_`` collapse to one; keys of
one host that end up with the same name each get ``_<itemid>`` appended.
"""
import math
import re

from monplat import history
from monplat.timeparse import parse_time  # noqa: F401 (part of this API)

ROOT = 'zabbix'
MONITORED = 0
HOST_FLAGS = (0, 4)
ITEM_FLAGS = (0, 4)
NUMERIC = tuple(sorted(history.TABLES))

_UNSAFE = re.compile(r'[^A-Za-z0-9_-]')
_UNDERSCORES = re.compile(r'_+')
_PATH = re.compile(r'^[A-Za-z0-9_*-]+(\.[A-Za-z0-9_*-]+)*$')
_ALIAS = re.compile(r'''^alias\(\s*([^,\s]+)\s*,\s*(["'])(.*)\2\s*\)$''')


class TargetError(ValueError):
    pass


def sanitise(name):
    return _UNDERSCORES.sub('_', _UNSAFE.sub('_', name))


def metric_path(host, key):
    """Return the Graphite path of item ``key`` on ``host``."""
    return '%s.%s.%s' % (ROOT, sanitise(host), sanitise(key))


def paths(rows):
    """Map ``(host, key, itemid)`` rows to ``{path: itemid}``, appending
    ``_<itemid>`` to every path that more than one item shares."""
    grouped = {}
    for host, key, itemid in rows:
        grouped.setdefault(metric_path(host, key), []).append(itemid)
    result = {}
    for path, itemids in grouped.items():
        if len(itemids) == 1:
            result[path] = itemids[0]
        else:
            for itemid in itemids:
                result['%s_%s' % (path, itemid)] = itemid
    return result


def metrics(conn):
    """Return ``{path: itemid}`` for the numeric items of monitored
    hosts."""
    cursor = conn.cursor()
    try:
        cursor.execute(
            'SELECT h.host, i.key_, i.itemid FROM items i '
            'JOIN hosts h ON h.hostid = i.hostid '
            'WHERE h.status = %s AND h.flags IN (%s, %s) '
            'AND i.flags IN (%s, %s) AND i.value_type IN (%s, %s)',
            (MONITORED,) + HOST_FLAGS + ITEM_FLAGS + NUMERIC)
        rows = cursor.fetchall()
    finally:
        cursor.close()
    return paths(rows)


def _pattern(query):
    """Compile a dotted query whose ``*`` matches within one segment."""
    parts = [re.escape(part) for part in query.split('.')]
    regex = r'\.'.join(part.replace(r'\*', '[^.]*') for part in parts)
    return re.compile('^%s$' % regex)


def _node(path, leaf):
    flag = 1 if leaf else 0
    return {'text': path.rsplit('.', 1)[-1], 'id': path, 'leaf': flag,
            'expandable': 1 - flag, 'allowChildren': 1 - flag}


def find(conn, query):
    """Return the Graphite nodes matching ``query``, sorted by id."""
    depth = query.count('.') + 1
    pattern = _pattern(query)
    nodes = {}
    for path in metrics(conn):
        segments = path.split('.')
        if depth > len(segments):
            continue
        prefix = '.'.join(segments[:depth])
        if pattern.match(prefix):
            nodes[prefix] = depth == len(segments)
    return [_node(path, nodes[path]) for path in sorted(nodes)]


def parse_target(target):
    """Split a render target into ``(path pattern, alias or None)``.
    Only plain paths with ``*`` and ``alias(path, "name")`` are
    supported."""
    text = target.strip()
    alias = None
    match = _ALIAS.match(text)
    if match:
        text, alias = match.group(1), match.group(3)
    if not _PATH.match(text):
        raise TargetError('unsupported target %r; only metric paths with '
                          '* and alias(path, "name") are supported'
                          % (target,))
    return text, alias


def _reduce(points, frm, until, max_points):
    """Average ``points`` into at most ``max_points`` aligned buckets."""
    if not max_points or len(points) <= max_points:
        return points
    span = max(until - frm, 1)
    # Aligned buckets over a span of n steps touch at most n + 1 buckets.
    step = int(math.ceil(float(span) / max(max_points - 1, 1)))
    return history.bucket(points, max(step, 1))


def render(conn, targets, frm, until, max_points):
    """Return ``[{"target", "datapoints": [[value, ts], ...]}]`` for each
    item matched by ``targets`` between ``frm`` and ``until``."""
    parsed = [parse_target(target) for target in targets]
    known = metrics(conn)
    series = []
    for pattern, alias in parsed:
        regex = _pattern(pattern)
        for path in sorted(p for p in known if regex.match(p)):
            points = history.fetch(conn, known[path], frm, until)
            points = _reduce(points, frm, until, max_points)
            series.append({'target': alias if alias is not None else path,
                           'datapoints': [[value, clock]
                                          for value, clock in points]})
    return series
