"""Zabbix sender protocol (ZBXD v1) for pushing values to trapper items.

A request is the 5-byte header ``ZBXD\\x01``, the payload length as an
8-byte little-endian integer, then a JSON document::

    {"request": "sender data",
     "data": [{"host": ..., "key": ..., "value": ..., "clock": ...}],
     "clock": <send time>}

The server answers with a frame of the same shape whose JSON ``info``
string summarises the result, e.g.
``processed: 2; failed: 1; total: 3; seconds spent: 0.000100``.

The reply is read with limits: a declared length above ``MAX_RESPONSE``
is refused before any of the body is read, and ``send()``'s ``timeout``
is one deadline for the whole exchange (connect, send and every read), so
a peer that trickles bytes cannot keep the caller waiting.
"""
import json
import re
import socket
import struct
import time

HEADER = b'ZBXD\x01'
_LENGTH = struct.Struct('<Q')
_PREFIX_SIZE = len(HEADER) + _LENGTH.size
# Trapper replies are a short JSON summary (well under 1 KiB).
MAX_RESPONSE = 64 * 1024
_CHUNK = 4096
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


def _read(sock, size, deadline):
    """Read up to ``size`` bytes, stopping early only at end of stream.
    Raises ``SenderError`` once ``deadline`` (epoch seconds) has passed."""
    buf = bytearray()
    while len(buf) < size:
        remaining = deadline - time.time()
        if remaining <= 0:
            raise SenderError('no complete response within the timeout '
                              '(%d of %d bytes received)' % (len(buf), size))
        sock.settimeout(remaining)
        chunk = sock.recv(min(_CHUNK, size - len(buf)))
        if not chunk:
            break
        buf.extend(chunk)
    return bytes(buf)


def _recv_frame(sock, deadline):
    """Read one ZBXD frame of at most ``MAX_RESPONSE`` body bytes."""
    prefix = _read(sock, _PREFIX_SIZE, deadline)
    if len(prefix) < _PREFIX_SIZE or prefix[:len(HEADER)] != HEADER:
        return prefix
    length = _LENGTH.unpack(prefix[len(HEADER):])[0]
    if length > MAX_RESPONSE:
        raise SenderError('response declares %d bytes, more than the %d '
                          'byte limit' % (length, MAX_RESPONSE))
    return prefix + _read(sock, length, deadline)


def send(server, port, data, timeout=10.0):
    """Send ``(host, key, value, clock)`` tuples to the trapper at
    ``server:port`` and return ``{'processed', 'failed', 'total'}``.
    ``timeout`` bounds the whole exchange, not each socket call."""
    frame = encode(data)
    deadline = time.time() + timeout
    try:
        sock = socket.create_connection((server, int(port)), timeout)
    except (socket.error, socket.timeout) as exc:
        raise SenderError('cannot connect to %s:%s: %s' % (server, port, exc))
    try:
        sock.settimeout(max(deadline - time.time(), 0.001))
        sock.sendall(frame)
        raw = _recv_frame(sock, deadline)
    except (socket.error, socket.timeout) as exc:
        raise SenderError('error talking to %s:%s: %s' % (server, port, exc))
    finally:
        sock.close()
    result = decode_response(raw)
    return dict((k, result[k]) for k in ('processed', 'failed', 'total'))
