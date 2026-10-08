"""Parse the time arguments used by the REST and Graphite endpoints.

Accepted forms are ``now``, epoch seconds and ``-<n><unit>`` relative to
``now``, with the Graphite units ``s``, ``min``, ``h``, ``d``, ``w``,
``mon`` (30 days) and ``y`` (365 days), plus a bare ``m`` for minutes.
Grafana's Graphite datasource turns ``now-5m`` into ``-5min`` and
``now-1M`` into ``-1mon`` and sends absolute ranges as epoch seconds.
"""
import re

UNITS = {
    's': 1,
    'min': 60,
    'm': 60,
    'h': 3600,
    'd': 86400,
    'w': 7 * 86400,
    'mon': 30 * 86400,
    'y': 365 * 86400,
}

_RELATIVE = re.compile(r'^-(\d+)(s|min|mon|m|h|d|w|y)$')
_EPOCH = re.compile(r'^\d+$')


class TimeParseError(ValueError):
    pass


def parse_time(s, now):
    """Return ``s`` as epoch seconds, taking relative forms from ``now``."""
    if not isinstance(s, basestring):
        raise TimeParseError('time must be a string, got %r' % (s,))
    text = s.strip()
    if text == 'now':
        return int(now)
    if _EPOCH.match(text):
        return int(text)
    match = _RELATIVE.match(text)
    if match is None:
        raise TimeParseError('cannot parse time %r; use now, epoch seconds '
                             'or -<n><unit> with unit one of %s'
                             % (s, ', '.join(sorted(UNITS))))
    count, unit = match.groups()
    return int(now) - int(count) * UNITS[unit]
