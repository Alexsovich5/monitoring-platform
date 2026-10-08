"""Flask application serving hosts, items and item history as JSON, plus
the Graphite find and render endpoints from ``monplat.api.graphite``.

Everything is read straight from the Zabbix PostgreSQL database; the
``monplat`` database is only checked by the health endpoint here.
"""
import contextlib
import json
import time

import psycopg2
from flask import Flask, Response, current_app, request

from monplat import config, db, history
from monplat.api import graphite
from monplat.timeparse import TimeParseError, parse_time

DEFAULT_FROM = '-1h'
# Graphite's own default render range is the last 24 hours.
RENDER_DEFAULT_FROM = '-1d'
DEFAULT_UNTIL = 'now'
MONITORED = 0
# hosts.flags: 0 = plain host, 4 = discovered host. Host prototypes (2)
# are templates for discovery, not hosts, and are left out.
HOST_FLAGS = (0, 4)


class BadRequest(Exception):
    status = 400


class NotFound(Exception):
    status = 404


def json_response(data, status=200):
    # Flask 0.10's jsonify refuses top-level lists, so serialise directly.
    return Response(json.dumps(data), status=status,
                    mimetype='application/json')


@contextlib.contextmanager
def connection(name='zabbix'):
    conn = db.connect(current_app.config['MONPLAT'], name)
    try:
        yield conn
    finally:
        conn.close()


def query(conn, sql, params):
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchall()
    finally:
        cursor.close()


def query_one(conn, sql, params):
    cursor = conn.cursor()
    try:
        cursor.execute(sql, params)
        return cursor.fetchone()
    finally:
        cursor.close()


def health():
    status = {}
    for name in ('zabbix', 'monplat'):
        try:
            db.connect(current_app.config['MONPLAT'], name).close()
            status['%s_db' % name] = True
        except psycopg2.Error:
            status['%s_db' % name] = False
    return json_response(status, 200 if all(status.values()) else 503)


def hosts():
    with connection() as conn:
        rows = query(conn, 'SELECT hostid, host FROM hosts '
                           'WHERE status = %s AND flags IN (%s, %s) '
                           'ORDER BY host', (MONITORED,) + HOST_FLAGS)
    return json_response([{'hostid': hostid, 'host': host}
                          for hostid, host in rows])


def host_items(host):
    with connection() as conn:
        row = query_one(conn, 'SELECT hostid FROM hosts '
                              'WHERE host = %s AND status = %s '
                              'AND flags IN (%s, %s)',
                        (host, MONITORED) + HOST_FLAGS)
        if row is None:
            raise NotFound('no monitored host %r' % host)
        rows = query(conn, 'SELECT itemid, key_, name, units, value_type '
                           'FROM items WHERE hostid = %s ORDER BY key_',
                     (row[0],))
    return json_response([{'itemid': itemid, 'key': key, 'name': name,
                           'units': units, 'value_type': value_type}
                          for itemid, key, name, units, value_type in rows])


def _time_arg(name, default, now):
    try:
        return parse_time(request.args.get(name, default), now)
    except TimeParseError as exc:
        raise BadRequest('%s: %s' % (name, exc))


def _step_arg():
    raw = request.args.get('step')
    if raw is None or raw == '':
        return None
    try:
        step = int(raw)
    except ValueError:
        step = 0
    if step <= 0:
        raise BadRequest('step must be a positive number of seconds, '
                         'got %r' % raw)
    return step


def item_history(itemid):
    now = int(time.time())
    start = _time_arg('from', DEFAULT_FROM, now)
    end = _time_arg('until', DEFAULT_UNTIL, now)
    if start > end:
        raise BadRequest('from (%d) is after until (%d)' % (start, end))
    step = _step_arg()
    with connection() as conn:
        try:
            points = history.fetch(conn, itemid, start, end, step=step)
        except history.UnknownItem as exc:
            raise NotFound(str(exc))
        except history.UnsupportedValueType as exc:
            raise BadRequest(str(exc))
    return json_response([{'clock': clock, 'value': value}
                          for value, clock in points])


def _form_time(name, default, now):
    try:
        return parse_time(request.values.get(name) or default, now)
    except TimeParseError as exc:
        raise BadRequest('%s: %s' % (name, exc))


def metrics_find():
    query_text = request.values.get('query')
    if not query_text:
        raise BadRequest('query is required, e.g. query=zabbix.*')
    with connection() as conn:
        nodes = graphite.find(conn, query_text)
    return json_response(nodes)


def render():
    fmt = request.values.get('format') or 'json'
    if fmt != 'json':
        raise BadRequest('format %r is not supported; only format=json'
                         % fmt)
    now = int(time.time())
    start = _form_time('from', RENDER_DEFAULT_FROM, now)
    end = _form_time('until', DEFAULT_UNTIL, now)
    if start > end:
        raise BadRequest('from (%d) is after until (%d)' % (start, end))
    raw = request.values.get('maxDataPoints')
    max_points = None
    if raw:
        try:
            max_points = int(raw)
        except ValueError:
            max_points = 0
        if max_points <= 0:
            raise BadRequest('maxDataPoints must be a positive integer, '
                             'got %r' % raw)
    targets = request.values.getlist('target')
    try:
        for target in targets:
            graphite.parse_target(target)
    except graphite.TargetError as exc:
        raise BadRequest(str(exc))
    with connection() as conn:
        series = graphite.render(conn, targets, start, end, max_points)
    return json_response(series)


def _error(exc):
    return json_response({'error': str(exc)}, exc.status)


def _not_found(exc):
    return json_response({'error': 'no such endpoint: %s' % request.path},
                         404)


def _allow_any_origin(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    return response


def create_app(cfg=None):
    """Build the API application; ``cfg`` defaults to ``config.load()``."""
    app = Flask(__name__)
    app.config['MONPLAT'] = config.load() if cfg is None else cfg
    app.add_url_rule('/api/v1/health', 'health', health)
    app.add_url_rule('/api/v1/hosts', 'hosts', hosts)
    app.add_url_rule('/api/v1/hosts/<host>/items', 'host_items', host_items)
    app.add_url_rule('/api/v1/items/<int:itemid>/history', 'item_history',
                     item_history)
    app.add_url_rule('/metrics/find/', 'metrics_find', metrics_find,
                     methods=['GET', 'POST'])
    app.add_url_rule('/render', 'render', render, methods=['GET', 'POST'])
    app.register_error_handler(BadRequest, _error)
    app.register_error_handler(NotFound, _error)
    app.register_error_handler(404, _not_found)
    app.after_request(_allow_any_origin)
    return app
