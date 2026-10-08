"""Zabbix alert notifications parsed into events and kept in ``monplat``.

``mp_alert.py`` posts the action's subject and message.  The message is
the ``key=value`` template that ``mpctl provision`` gives the action
"MP notify API" (see ``config/actions.yml``)::

    eventid={EVENT.ID}
    status={TRIGGER.STATUS}
    host={HOST.NAME}
    trigger_id={TRIGGER.ID}
    trigger_name={TRIGGER.NAME}
    severity={TRIGGER.NSEVERITY}
    value={ITEM.VALUE}
    time={EVENT.DATE} {EVENT.TIME}

A recovery message carries the PROBLEM event's ``{EVENT.ID}`` with status
``OK``, so ``(eventid, status)`` identifies one notification.  Zabbix
writes ``{EVENT.DATE} {EVENT.TIME}`` in the server's local time, which is
UTC in the zabbix container.
"""
import collections
import datetime

import psycopg2
from psycopg2.tz import FixedOffsetTimezone

STATUSES = ('PROBLEM', 'OK')
TIME_FORMAT = '%Y.%m.%d %H:%M:%S'
UTC = FixedOffsetTimezone(offset=0)
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000

Event = collections.namedtuple(
    'Event', 'eventid status host trigger_id trigger_name severity '
             'item_value event_time')

COLUMNS = ('id', 'eventid', 'status', 'host', 'trigger_id', 'trigger_name',
           'severity', 'item_value', 'event_time', 'received_at', 'notified')

_INSERT = (
    'INSERT INTO events (eventid, status, host, trigger_id, trigger_name, '
    'severity, item_value, event_time) '
    'SELECT %(eventid)s, %(status)s, %(host)s, %(trigger_id)s, '
    '%(trigger_name)s, %(severity)s, %(item_value)s, %(event_time)s '
    'WHERE NOT EXISTS (SELECT 1 FROM events '
    'WHERE eventid = %(eventid)s AND status = %(status)s) '
    'RETURNING id')
_SELECT_ID = ('SELECT id FROM events '
              'WHERE eventid = %(eventid)s AND status = %(status)s')


class EventError(ValueError):
    """The notification cannot be turned into an event (HTTP 400)."""


def _fields(body):
    fields = {}
    for line in body.splitlines():
        if '=' not in line:
            continue
        key, value = line.split('=', 1)
        fields[key.strip()] = value.strip()
    return fields


def _required(fields, name):
    value = fields.get(name)
    if not value:
        raise EventError('alert body has no %s= line' % name)
    return value


def _integer(fields, name):
    raw = _required(fields, name)
    try:
        return int(raw)
    except ValueError:
        raise EventError('%s must be an integer, got %r' % (name, raw))


def parse_alert(subject, body):
    """Return the ``Event`` described by an alert ``body``.  ``subject``
    is only used in error messages.  Raises ``EventError``."""
    if not body:
        raise EventError('alert body is empty (subject %r)' % subject)
    fields = _fields(body)
    eventid = _integer(fields, 'eventid')
    status = _required(fields, 'status')
    if status not in STATUSES:
        raise EventError('status must be PROBLEM or OK, got %r' % status)
    severity_raw = fields.get('severity', '')
    try:
        severity = int(severity_raw)
    except ValueError:
        severity = -1
    if not 0 <= severity <= 5:
        raise EventError('severity must be {TRIGGER.NSEVERITY} (0-5), '
                         'got %r' % severity_raw)
    raw_time = _required(fields, 'time')
    try:
        event_time = datetime.datetime.strptime(raw_time, TIME_FORMAT)
    except ValueError:
        raise EventError('time must look like "2014.12.18 12:34:56", '
                         'got %r' % raw_time)
    return Event(eventid=eventid,
                 status=status,
                 host=_required(fields, 'host'),
                 trigger_id=_integer(fields, 'trigger_id'),
                 trigger_name=_required(fields, 'trigger_name'),
                 severity=severity,
                 item_value=fields.get('value') or None,
                 event_time=event_time.replace(tzinfo=UTC))


def store(conn, event):
    """Insert ``event`` unless its ``(eventid, status)`` is already
    stored; return the row id either way."""
    params = event._asdict()
    cursor = conn.cursor()
    try:
        try:
            cursor.execute(_INSERT, params)
            row = cursor.fetchone()
        except psycopg2.IntegrityError:
            # A concurrent insert of the same notification won the race.
            conn.rollback()
            row = None
        if row is None:
            cursor.execute(_SELECT_ID, params)
            row = cursor.fetchone()
        conn.commit()
    finally:
        cursor.close()
    return row[0]


def recent(conn, host=None, status=None, limit=DEFAULT_LIMIT):
    """Return stored events as dicts, newest first."""
    where, params = [], []
    if host:
        where.append('host = %s')
        params.append(host)
    if status:
        where.append('status = %s')
        params.append(status)
    sql = 'SELECT %s FROM events' % ', '.join(COLUMNS)
    if where:
        sql += ' WHERE ' + ' AND '.join(where)
    sql += ' ORDER BY received_at DESC, id DESC LIMIT %s'
    params.append(limit)
    cursor = conn.cursor()
    try:
        cursor.execute(sql, tuple(params))
        rows = cursor.fetchall()
    finally:
        cursor.close()
    result = []
    for row in rows:
        event = dict(zip(COLUMNS, row))
        for field in ('event_time', 'received_at'):
            event[field] = event[field].isoformat()
        result.append(event)
    return result
