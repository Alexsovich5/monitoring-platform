"""Thin helpers around ``pyzabbix.ZabbixAPI``.

``connect()`` logs in to the frontend's JSON-RPC API and keeps retrying
while the frontend is still starting (connection refused, HTTP errors, or
an HTML page instead of JSON).  Errors returned by the API itself, such as
a wrong password, are raised at once.

``get_or_create()`` makes provisioning idempotent: it looks an object up by
a filter and only calls ``<object>.create`` when nothing matches.
"""
import logging
import time

import requests
from pyzabbix import ZabbixAPI, ZabbixAPIException

# pyzabbix logs every JSON-RPC request and response at DEBUG, including the
# user.login password and the session token in "auth".  Keep that logger
# above DEBUG whatever level the application runs at.
logging.getLogger('pyzabbix').setLevel(logging.INFO)

# API objects whose id field is not simply "<object>id".
ID_FIELDS = {
    'hostgroup': 'groupid',
    'usergroup': 'usrgrpid',
    'usermacro': 'hostmacroid',
    'globalmacro': 'globalmacroid',
}

_TRANSIENT_API_ERRORS = ('Unable to parse json', 'Received empty response')


class ZabbixUnavailable(Exception):
    pass


def _transient(exc):
    if isinstance(exc, (requests.ConnectionError, requests.Timeout,
                        requests.HTTPError)):
        return True
    if isinstance(exc, ZabbixAPIException):
        return str(exc).startswith(_TRANSIENT_API_ERRORS)
    return False


def connect(cfg, retries=30, delay=2, timeout=10):
    """Return a logged-in ``ZabbixAPI`` for ``cfg['zabbix']``.

    Makes up to ``retries`` login attempts, sleeping ``delay`` seconds
    between them, and raises ``ZabbixUnavailable`` when all of them fail.
    """
    zcfg = cfg['zabbix']
    url = zcfg['url'].rstrip('/')
    last = None
    for attempt in range(1, retries + 1):
        zapi = ZabbixAPI(url, timeout=timeout)
        try:
            zapi.login(zcfg['user'], zcfg['password'])
            return zapi
        except Exception as exc:
            if not _transient(exc):
                raise
            last = exc
        if attempt < retries:
            time.sleep(delay)
    raise ZabbixUnavailable('Zabbix API at %s not reachable after %d '
                            'attempts: %s' % (url, retries, last))


def id_field(obj):
    return ID_FIELDS.get(obj, obj + 'id')


def get_or_create(zapi, obj, filter, params):
    """Return ``(id, created)`` for the ``obj`` matching ``filter``,
    creating it from ``params`` when it does not exist yet."""
    api_obj = getattr(zapi, obj)
    field = id_field(obj)
    found = api_obj.get(filter=filter, output='extend')
    if found:
        return found[0][field], False
    result = api_obj.create(**params)
    return result[field + 's'][0], True
