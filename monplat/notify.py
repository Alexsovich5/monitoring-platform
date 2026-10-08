"""Forward events to a Pushover-compatible push API.

The ``notify`` config section names the endpoint (``pushover_url``) and
the application ``token`` and ``user`` key.  Zabbix severities map onto
push priorities: 0-1 (not classified, information) -> -1 (quiet),
2-3 (warning, average) -> 0 (normal), 4-5 (high, disaster) -> 1 (high).
"""
import logging
import sys

import requests

TIMEOUT = 10
_PRIORITIES = {0: -1, 1: -1, 2: 0, 3: 0, 4: 1, 5: 1}

log = logging.getLogger(__name__)


def priority(severity):
    """Return the push priority for a Zabbix severity (0-5)."""
    try:
        return _PRIORITIES[severity]
    except KeyError:
        raise ValueError('severity must be 0-5, got %r' % (severity,))


def title(event):
    return '%s: %s' % (event.status, event.trigger_name)


def message(event):
    lines = ['Host: %s' % event.host,
             'Severity: %d' % event.severity,
             'Time: %s' % event.event_time.strftime('%Y-%m-%d %H:%M:%S %Z')]
    if event.item_value is not None:
        lines.insert(2, 'Value: %s' % event.item_value)
    lines.append('Event ID: %d' % event.eventid)
    return '\n'.join(lines)


def push(cfg, event):
    """Send ``event`` as one push message.  Return True when the API
    answers 200; log and return False on any other outcome."""
    settings = cfg.get('notify') or {}
    url = settings.get('pushover_url')
    if not url:
        log.warning('notify.pushover_url is not configured; '
                    'event %s not pushed', event.eventid)
        return False
    data = {'token': settings.get('token'),
            'user': settings.get('user'),
            'title': title(event),
            'message': message(event),
            'priority': priority(event.severity)}
    try:
        response = requests.post(url, data=data, timeout=TIMEOUT)
    except requests.RequestException as exc:
        sys.stderr.write('push to %s failed: %s\n' % (url, exc))
        return False
    if response.status_code != 200:
        sys.stderr.write('push to %s returned %d: %s\n'
                         % (url, response.status_code, response.text[:200]))
        return False
    return True
