import json
import socket
import struct
import threading

import pytest

from monplat import cli
from monplat.zabbix import sender


def _frame(payload):
    return b'ZBXD\x01' + struct.pack('<Q', len(payload)) + payload


def _read_exact(conn, size):
    data = b''
    while len(data) < size:
        chunk = conn.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


class FakeTrapper(object):
    """A one-shot trapper that records the request and replies with a
    canned ZBXD response."""

    def __init__(self, response):
        self.response = response
        self.request = None
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self.sock.bind(('127.0.0.1', 0))
        self.sock.listen(1)
        self.port = self.sock.getsockname()[1]
        self.thread = threading.Thread(target=self._serve)
        self.thread.daemon = True
        self.thread.start()

    def _serve(self):
        conn, _ = self.sock.accept()
        try:
            header = _read_exact(conn, 13)
            length = struct.unpack('<Q', header[5:13])[0]
            self.request = header + _read_exact(conn, length)
            conn.sendall(self.response)
        finally:
            conn.close()
            self.sock.close()


def _free_port():
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_encode_builds_zbxd_v1_frame():
    raw = sender.encode([('web01', 'mp.cpu.util', 12.5, 1400000000)])
    assert raw[:5] == b'ZBXD\x01'
    length = struct.unpack('<Q', raw[5:13])[0]
    body = raw[13:]
    assert length == len(body)
    doc = json.loads(body.decode('utf-8'))
    assert doc['request'] == 'sender data'
    assert isinstance(doc['clock'], int)
    assert doc['data'] == [{'host': 'web01', 'key': 'mp.cpu.util',
                            'value': '12.5', 'clock': 1400000000}]


def test_encode_omits_clock_for_values_without_one():
    raw = sender.encode([('web01', 'mp.cpu.util', 3, None)])
    doc = json.loads(raw[13:].decode('utf-8'))
    assert doc['data'] == [{'host': 'web01', 'key': 'mp.cpu.util',
                            'value': '3'}]


def test_decode_response_parses_summary():
    payload = json.dumps({
        'response': 'success',
        'info': 'processed: 2; failed: 1; total: 3; '
                'seconds spent: 0.0001'}).encode('utf-8')
    result = sender.decode_response(_frame(payload))
    assert result['processed'] == 2
    assert result['failed'] == 1
    assert result['total'] == 3
    assert abs(result['seconds'] - 0.0001) < 1e-9


def test_decode_response_rejects_bad_header():
    with pytest.raises(sender.SenderError):
        sender.decode_response(b'HTTP/1.1 400 Bad Request\r\n\r\n')


def test_decode_response_rejects_truncated_body():
    payload = b'{"response": "success"}'
    raw = b'ZBXD\x01' + struct.pack('<Q', len(payload) + 10) + payload
    with pytest.raises(sender.SenderError):
        sender.decode_response(raw)


def test_send_round_trip_against_fake_trapper():
    reply = _frame(json.dumps({
        'response': 'success',
        'info': 'processed: 1; failed: 1; total: 2; '
                'seconds spent: 0.000050'}).encode('utf-8'))
    trapper = FakeTrapper(reply)
    result = sender.send('127.0.0.1', trapper.port, [
        ('mp-collector', 'mp.cpu.util', 7.25, 1400000000),
        ('mp-collector', 'no.such.key', 1, 1400000001),
    ])
    trapper.thread.join(5)
    assert result == {'processed': 1, 'failed': 1, 'total': 2}
    doc = json.loads(trapper.request[13:].decode('utf-8'))
    assert doc['request'] == 'sender data'
    assert [d['key'] for d in doc['data']] == ['mp.cpu.util', 'no.such.key']
    assert doc['data'][0]['value'] == '7.25'


def test_send_raises_sender_error_when_nothing_listens():
    with pytest.raises(sender.SenderError):
        sender.send('127.0.0.1', _free_port(), [('h', 'k', 1, None)])


def test_cli_send_uses_configured_trapper(monkeypatch, capsys):
    reply = _frame(json.dumps({
        'response': 'success',
        'info': 'processed: 1; failed: 0; total: 1; '
                'seconds spent: 0.000020'}).encode('utf-8'))
    trapper = FakeTrapper(reply)
    monkeypatch.setenv('MONPLAT_ZABBIX_SERVER', '127.0.0.1')
    monkeypatch.setenv('MONPLAT_ZABBIX_PORT', str(trapper.port))
    code = cli.main(['send', 'mp-collector', 'mp.cpu.util', '42',
                     '--clock', '1400000000'])
    trapper.thread.join(5)
    assert code == 0
    doc = json.loads(trapper.request[13:].decode('utf-8'))
    assert doc['data'] == [{'host': 'mp-collector', 'key': 'mp.cpu.util',
                            'value': '42', 'clock': 1400000000}]
    out, _ = capsys.readouterr()
    assert 'processed: 1' in out


def test_cli_send_exits_one_when_nothing_listens(monkeypatch, capsys):
    monkeypatch.setenv('MONPLAT_ZABBIX_SERVER', '127.0.0.1')
    monkeypatch.setenv('MONPLAT_ZABBIX_PORT', str(_free_port()))
    code = cli.main(['send', 'mp-collector', 'mp.cpu.util', '42'])
    assert code == 1
    _, err = capsys.readouterr()
    assert 'mpctl send' in err


def test_cli_send_exits_one_when_server_rejects_value(monkeypatch, capsys):
    reply = _frame(json.dumps({
        'response': 'success',
        'info': 'processed: 0; failed: 1; total: 1; '
                'seconds spent: 0.000020'}).encode('utf-8'))
    trapper = FakeTrapper(reply)
    monkeypatch.setenv('MONPLAT_ZABBIX_SERVER', '127.0.0.1')
    monkeypatch.setenv('MONPLAT_ZABBIX_PORT', str(trapper.port))
    code = cli.main(['send', 'mp-collector', 'no.such.key', '1'])
    trapper.thread.join(5)
    assert code == 1
    out, _ = capsys.readouterr()
    assert 'failed: 1' in out
