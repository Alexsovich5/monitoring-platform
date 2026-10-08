"""Fixtures and helpers for tests that run against the compose stack."""
import socket
import struct
import time
import xmlrpclib

import pytest

from monplat import config
from monplat.zabbix import api, sender

STOCK_HOST = 'Zabbix server'


@pytest.fixture(scope='session')
def cfg():
    return config.load()


@pytest.fixture(scope='session')
def zapi(cfg):
    """A logged-in API client.  Also enables the stock "Zabbix server"
    host, which 2.4's data.sql ships disabled, so that the server starts
    polling its own agent."""
    zapi = api.connect(cfg, retries=60)
    hosts = zapi.host.get(filter={'host': STOCK_HOST},
                          output=['hostid', 'status'])
    if hosts and hosts[0]['status'] != '0':
        zapi.host.update(hostid=hosts[0]['hostid'], status=0)
    return zapi


def wait_for(check, timeout, interval=2, message='condition not met'):
    """Call ``check`` until it returns a true value or ``timeout`` seconds
    pass; return that value or fail the test with ``message`` and the last
    value seen."""
    deadline = time.time() + timeout
    last = None
    while True:
        last = check()
        if last:
            return last
        if time.time() >= deadline:
            pytest.fail('%s within %ss (last: %r)' % (message, timeout, last))
        time.sleep(interval)


def send_until_processed(cfg, rows, timeout=60, interval=2):
    """Send ``rows`` with the sender protocol until the trapper reports
    ``processed == total``.  Newly created trapper items are only accepted
    once the server's configuration cache has picked them up."""
    zcfg = cfg['zabbix']
    deadline = time.time() + timeout
    result = None
    while True:
        try:
            result = sender.send(zcfg['server'], zcfg['port'], rows)
            if result['processed'] == result['total']:
                return result
        except sender.SenderError as exc:
            result = exc
        if time.time() >= deadline:
            pytest.fail('trapper did not process %r within %ss (last: %r)'
                        % (rows, timeout, result))
        time.sleep(interval)


def supervisor_processes(host='zabbix', port=9001):
    """Return supervisord's process table (what ``supervisorctl status``
    shows) from the XML-RPC interface."""
    proxy = xmlrpclib.ServerProxy('http://%s:%d/RPC2' % (host, port))
    return proxy.supervisor.getAllProcessInfo()


def agent_get(key, host='zabbix', port=10050, timeout=10):
    """Ask a Zabbix agent for ``key`` with a passive check, like
    ``zabbix_get``, and return the value as a string."""
    sock = socket.create_connection((host, port), timeout)
    try:
        sock.sendall(key + '\n')
        data = b''
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
    finally:
        sock.close()
    if data[:5] != b'ZBXD\x01':
        raise AssertionError('agent reply has no ZBXD header: %r' % data)
    length = struct.unpack('<Q', data[5:13])[0]
    return data[13:13 + length]
