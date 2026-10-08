"""Zabbix sender protocol (ZBXD v1) for pushing values to trapper items.

A request is the 5-byte header ``ZBXD\\x01``, the payload length as an
8-byte little-endian integer, then a JSON document::

    {"request": "sender data",
     "data": [{"host": ..., "key": ..., "value": ..., "clock": ...}],
     "clock": <send time>}

The server answers with a frame of the same shape whose JSON ``info``
string summarises the result, e.g.
``processed: 2; failed: 1; total: 3; seconds spent: 0.000100``.
"""
import json
import re
import socket
import struct
import time

HEADER = b'ZBXD\x01'
_LENGTH = struct.Struct('<Q')
_PREFIX_SIZE = len(HEADER) + _LENGTH.size
_INFO_RE = re.compile(
    r'processed:?\s*(\d+);\s*failed:?\s*(\d+);\s*total:?\s*(\d+);'
    r'\s*seconds spent:?\s*([0-9.]+)')


class SenderError(Exception):
    pass


def _text(value):
    if isinstance(value, bytes):
        return value.decode('utf-8')
    if isinstance(value, float):
        return repr(value)
    return u'%s' % (value,)


def encode(data, now=None):
    """Return the ZBXD frame for ``data``, a list of
    ``(host, key, value, clock)`` tuples.  ``clock`` may be ``None`` to let
    the server stamp the value with its receive time."""
    items = []
    for host, key, value, clock in data:
        item = {'host': _text(host), 'key': _text(key), 'value': _text(value)}
        if clock is not None:
            item['clock'] = int(clock)
        items.append(item)
    body = json.dumps({
        'request': 'sender data',
        'data': items,
        'clock': int(time.time() if now is None else now),
    }).encode('utf-8')
    return HEADER + _LENGTH.pack(len(body)) + body


def decode_response(raw):
    """Parse a server response frame into
    ``{'processed', 'failed', 'total', 'seconds', 'response', 'info'}``."""
    if len(raw) < _PREFIX_SIZE or raw[:len(HEADER)] != HEADER:
        raise SenderError('response does not start with a ZBXD v1 header')
    length = _LENGTH.unpack(raw[len(HEADER):_PREFIX_SIZE])[0]
    body = raw[_PREFIX_SIZE:]
    if len(body) < length:
        raise SenderError('response truncated: expected %d bytes, got %d'
                          % (length, len(body)))
    try:
        doc = json.loads(body[:length].decode('utf-8'))
    except ValueError as exc:
        raise SenderError('response is not valid JSON: %s' % exc)
    status = doc.get('response')
    info = doc.get('info', '')
    if status != 'success':
        raise SenderError('server answered %r: %s' % (status, info))
    match = _INFO_RE.search(info)
    if not match:
        raise SenderError('cannot parse response info %r' % info)
    return {
        'response': status,
        'info': info,
        'processed': int(match.group(1)),
        'failed': int(match.group(2)),
        'total': int(match.group(3)),
        'seconds': float(match.group(4)),
    }


def _recv_frame(sock):
    data = b''
    expected = None
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            break
        data += chunk
        if expected is None and len(data) >= _PREFIX_SIZE:
            if data[:len(HEADER)] != HEADER:
                break
            expected = _PREFIX_SIZE + _LENGTH.unpack(
                data[len(HEADER):_PREFIX_SIZE])[0]
        if expected is not None and len(data) >= expected:
            break
    return data


def send(server, port, data, timeout=10.0):
    """Send ``(host, key, value, clock)`` tuples to the trapper at
    ``server:port`` and return ``{'processed', 'failed', 'total'}``."""
    frame = encode(data)
    try:
        sock = socket.create_connection((server, int(port)), timeout)
    except (socket.error, socket.timeout) as exc:
        raise SenderError('cannot connect to %s:%s: %s' % (server, port, exc))
    try:
        sock.sendall(frame)
        raw = _recv_frame(sock)
    except (socket.error, socket.timeout) as exc:
        raise SenderError('error talking to %s:%s: %s' % (server, port, exc))
    finally:
        sock.close()
    result = decode_response(raw)
    return dict((k, result[k]) for k in ('processed', 'failed', 'total'))
