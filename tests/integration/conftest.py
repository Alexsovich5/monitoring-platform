"""Fixtures and helpers for tests that run against the compose stack."""
import socket
import struct
import time
import xmlrpclib

import pytest

from monplat import config, intake, templates
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


def intake_headers(cfg):
    """The header that authenticates a POST to /api/v1/events, read from
    the token file that the Makefile's provision step created."""
    token = intake.read_token(intake.token_path(cfg))
    if not token:
        pytest.fail('no intake token; run "mpctl provision" first')
    return {intake.HEADER: token}


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


LINUX_TEMPLATE = 'Template MP Linux'
TEST_HOST_GROUP = 'MP Servers'
TEST_HOSTS = ('mp-test-collect', 'mp-test-alert', 'mp-test-forecast')


@pytest.fixture(scope='session')
def test_hosts(zapi):
    """Trapper-only hosts, one per integration module that sends data,
    each in group MP Servers and linked to Template MP Linux.  The
    template itself comes from the Makefile's ``mpctl provision`` step.
    Returns ``{host name: hostid}``."""
    found = zapi.template.get(filter={'host': LINUX_TEMPLATE},
                              output=['templateid'])
    if not found:
        pytest.fail('%s is missing; run "mpctl provision" first'
                    % LINUX_TEMPLATE)
    templateid = found[0]['templateid']
    groupid, _ = api.get_or_create(zapi, 'hostgroup',
                                   {'name': TEST_HOST_GROUP},
                                   {'name': TEST_HOST_GROUP})
    hostids = {}
    for name in TEST_HOSTS:
        hosts = zapi.host.get(filter={'host': name}, output=['hostid'],
                              selectParentTemplates=['templateid'])
        if not hosts:
            created = zapi.host.create(host=name,
                                       groups=[{'groupid': groupid}],
                                       interfaces=[dict(
                                           templates.TRAPPER_ONLY_INTERFACE)])
            hostid = created['hostids'][0]
            linked = False
        else:
            hostid = hosts[0]['hostid']
            linked = templateid in [t['templateid']
                                    for t in hosts[0]['parentTemplates']]
        if not linked:
            zapi.host.massadd(hosts=[{'hostid': hostid}],
                              templates=[{'templateid': templateid}])
        hostids[name] = hostid
    return hostids
