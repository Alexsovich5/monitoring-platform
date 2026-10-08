"""Zabbix alert notifications parsed into events and kept in ``monplat``.

``mp_alert.py`` posts the action's subject and message.  The message is
the ``key=value`` template that ``mpctl provision`` gives the action
"MP notify API" (see ``config/actions.yml``)::

    eventid={EVENT.ID}
    status={TRIGGER.STATUS}
    host={HOST.HOST}
    trigger_id={TRIGGER.ID}
    trigger_name={TRIGGER.NAME}
    severity={TRIGGER.NSEVERITY}
    time={EVENT.DATE} {EVENT.TIME}
    value={ITEM.VALUE}

Item values (and trigger names that embed them) come from monitored hosts,
so the parser is strict: every line before ``value=`` must be one of the
known keys, given once; each field is validated; and ``value=`` is the
last key, with everything after it taken as the value, so a value holding
``"\nstatus=OK"`` stays inside the value instead of becoming a field.

A recovery message carries the PROBLEM event's ``{EVENT.ID}`` with status
``OK``, so ``(eventid, status)`` identifies one notification.  Zabbix
writes ``{EVENT.DATE} {EVENT.TIME}`` in the server's local time, which is
UTC in the zabbix container.
"""
import collections
import datetime
import re

import psycopg2
from psycopg2.tz import FixedOffsetTimezone

STATUSES = ('PROBLEM', 'OK')
TIME_FORMAT = '%Y.%m.%d %H:%M:%S'
UTC = FixedOffsetTimezone(offset=0)
DEFAULT_LIMIT = 100
MAX_LIMIT = 1000
# Largest accepted alert body, in characters.
MAX_BODY = 8192
# Column sizes in sql/monplat_schema.sql.
MAX_TRIGGER_NAME = 255
MAX_ITEM_VALUE = 255
MAX_HOST = 128

KEYS = ('eventid', 'status', 'host', 'trigger_id', 'trigger_name',
        'severity', 'time', 'value')
VALUE_KEY = 'value'
_DIGITS = re.compile(r'^[0-9]+$')
# Zabbix technical host names: letters, digits, space, dot, dash, underscore.
_HOST = re.compile(r'^[0-9A-Za-z _.-]+$')

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
    """Split ``body`` into ``{key: value}``; see the module docstring."""
    fields = {}
    rest = body
    while rest:
        line, newline, rest = rest.partition('\n')
        line = line.rstrip('\r')
        if not line.strip():
            continue
        if '=' not in line:
            raise EventError('alert body line %r is not key=value' % line)
        key, value = line.split('=', 1)
        key = key.strip()
        if key not in KEYS:
            raise EventError('alert body has an unknown key %r' % key)
        if key in fields:
            raise EventError('alert body has a duplicate %s= line' % key)
        if key == VALUE_KEY:
            fields[key] = (value + newline + rest).strip()
            break
        fields[key] = value.strip()
    if VALUE_KEY not in fields:
        raise EventError('alert body has no value= line (it must be last)')
    return fields


def _required(fields, name):
    value = fields.get(name)
    if not value:
        raise EventError('alert body has no %s= line' % name)
    return value


def _integer(fields, name):
    raw = _required(fields, name)
    if not _DIGITS.match(raw):
        raise EventError('%s must be a non-negative integer, got %r'
                         % (name, raw))
    return int(raw)


def _host(fields):
    host = fields.get('host') or ''
    if not _HOST.match(host) or len(host) > MAX_HOST:
        raise EventError('host must be a Zabbix host name (letters, digits, '
                         'space, ".", "-", "_"; at most %d), got %r'
                         % (MAX_HOST, host))
    return host


def parse_alert(subject, body):
    """Return the ``Event`` described by an alert ``body``.  ``subject``
    is only used in error messages.  Raises ``EventError``."""
    if not body:
        raise EventError('alert body is empty (subject %r)' % subject)
    if len(body) > MAX_BODY:
        raise EventError('alert body is too large (%d characters, at most '
                         '%d)' % (len(body), MAX_BODY))
    fields = _fields(body)
    eventid = _integer(fields, 'eventid')
    status = _required(fields, 'status')
    if status not in STATUSES:
        raise EventError('status must be PROBLEM or OK, got %r' % status)
    severity_raw = fields.get('severity', '')
    severity = int(severity_raw) if _DIGITS.match(severity_raw) else -1
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
                 host=_host(fields),
                 trigger_id=_integer(fields, 'trigger_id'),
                 trigger_name=_required(fields, 'trigger_name')
                 [:MAX_TRIGGER_NAME],
                 severity=severity,
                 item_value=fields[VALUE_KEY][:MAX_ITEM_VALUE] or None,
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


def _update_notified(conn, sql, event_id):
    cursor = conn.cursor()
    try:
        cursor.execute(sql, (event_id,))
        row = cursor.fetchone()
        conn.commit()
    finally:
        cursor.close()
    return row


def claim_notification(conn, event_id):
    """Mark event ``event_id`` as notified if it is not already.  Return
    True only for the caller that flipped the flag, so a repeated POST of
    one notification is pushed once."""
    row = _update_notified(conn, 'UPDATE events SET notified = true '
                                 'WHERE id = %s AND NOT notified '
                                 'RETURNING id', event_id)
    return row is not None


def release_notification(conn, event_id):
    """Clear the notified flag after a push that failed."""
    _update_notified(conn, 'UPDATE events SET notified = false '
                           'WHERE id = %s RETURNING id', event_id)


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
