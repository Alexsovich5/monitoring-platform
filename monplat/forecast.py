"""Forecast when an item reaches a threshold and push the hours left.

Zabbix 2.4 has no ``forecast()``/``timeleft()`` trigger functions, so the
prediction is computed here: ``fit()`` fits a least-squares line through the
recent history of an item, ``hours_to_threshold()`` extends that line to the
threshold, and ``run()`` sends the result to a trapper item (by default
``mp.forecast.hours_left[/]``) whose trigger fires inside the horizon.

Points are ``(value, clock)`` tuples, as returned by ``monplat.history``.
An item whose series is flat, falling or too short has no forecast; no value
is sent for it in that round.
"""
import sys
import time

from monplat import db, history
from monplat.zabbix import sender

DEFAULT_INTERVAL = 300
DEFAULT_WINDOW_HOURS = 6
DEFAULT_TARGET_KEY = 'mp.forecast.hours_left[/]'
MIN_POINTS = 3
HOUR = 3600.0


def fit(points):
    """Return ``(slope, intercept)`` of the least-squares line
    ``value = slope * clock + intercept`` (slope per second).  Raises
    ``ValueError`` when the points do not span two distinct clocks."""
    count = len(points)
    if count < 2:
        raise ValueError('need at least two points, got %d' % count)
    mean_x = sum(float(clock) for _, clock in points) / count
    mean_y = sum(float(value) for value, _ in points) / count
    sxx = sum((clock - mean_x) ** 2 for _, clock in points)
    if sxx == 0:
        raise ValueError('points share a single clock')
    sxy = sum((clock - mean_x) * (value - mean_y) for value, clock in points)
    slope = sxy / sxx
    return slope, mean_y - slope * mean_x


def hours_to_threshold(points, threshold, now):
    """Return the hours from ``now`` until the fitted line reaches
    ``threshold``: 0 when the latest value is already at or above it, and
    ``None`` for fewer than three points or a flat or falling series."""
    if len(points) < MIN_POINTS:
        return None
    latest = max(points, key=lambda point: point[1])[0]
    if latest >= threshold:
        return 0.0
    try:
        slope, intercept = fit(points)
    except ValueError:
        return None
    if slope <= 0:
        return None
    seconds = (threshold - (slope * now + intercept)) / slope
    return max(seconds / HOUR, 0.0)


def lookup_itemid(conn, host, key):
    """Return the itemid of ``key`` on monitored ``host``, or None."""
    cursor = conn.cursor()
    try:
        cursor.execute('SELECT i.itemid FROM items i '
                       'JOIN hosts h ON h.hostid = i.hostid '
                       'WHERE h.host = %s AND i.key_ = %s AND h.status = 0',
                       (host, key))
        row = cursor.fetchone()
    finally:
        cursor.close()
    return row[0] if row else None


def _fetch(conn, itemid, start, end):
    return history.fetch(conn, itemid, start, end)


def forecast_rows(cfg, conn, now, lookup=lookup_itemid, fetch=_fetch):
    """Return sender rows ``(host, target_key, hours, now)`` for every
    configured item that has a forecast."""
    fcfg = cfg.get('forecast') or {}
    window = float(fcfg.get('window_hours', DEFAULT_WINDOW_HOURS)) * HOUR
    rows = []
    for item in fcfg.get('items') or []:
        host, key = item['host'], item['key']
        itemid = lookup(conn, host, key)
        if itemid is None:
            sys.stderr.write('mpctl forecast: no item %s on %s\n'
                             % (key, host))
            continue
        points = fetch(conn, itemid, int(now - window), int(now))
        hours = hours_to_threshold(points, float(item['threshold']), now)
        if hours is None:
            continue
        rows.append((host, item.get('target_key', DEFAULT_TARGET_KEY),
                     round(hours, 4), int(now)))
    return rows


def _round(cfg, connect, lookup, fetch, send, now):
    conn = connect(cfg)
    try:
        rows = forecast_rows(cfg, conn, now(), lookup=lookup, fetch=fetch)
    finally:
        conn.close()
    if not rows:
        return {'processed': 0, 'failed': 0, 'total': 0}
    zcfg = cfg.get('zabbix') or {}
    return send(zcfg.get('server', 'localhost'),
                int(zcfg.get('port', 10051)), rows)


def run(cfg, once=False, interval=DEFAULT_INTERVAL, connect=None,
        lookup=lookup_itemid, fetch=_fetch, send=None, sleep=None, now=None):
    """Forecast every configured item and send the hours left.  With
    ``once`` do a single round and return the sender result (errors
    propagate); otherwise repeat every ``interval`` seconds forever,
    reporting errors on stderr."""
    connect = connect or db.connect
    send = send or sender.send
    sleep = sleep or time.sleep
    now = now or time.time
    if once:
        return _round(cfg, connect, lookup, fetch, send, now)
    while True:
        try:
            result = _round(cfg, connect, lookup, fetch, send, now)
            sys.stdout.write('processed: %d; failed: %d; total: %d\n'
                             % (result['processed'], result['failed'],
                                result['total']))
        except Exception as exc:  # keep the service alive across outages
            sys.stderr.write('mpctl forecast: %s\n' % exc)
        sys.stdout.flush()
        sleep(interval)
