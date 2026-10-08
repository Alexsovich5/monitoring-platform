#!/usr/bin/env python
"""Zabbix alertscript for the "MP API" media type.

Zabbix runs it as ``mp_alert.py {ALERT.SENDTO} {ALERT.SUBJECT}
{ALERT.MESSAGE}``.  The send-to address of the user media is the API's
``/api/v1/events`` URL; the subject and message are posted there as a
form, with the intake token from ``TOKEN_FILE`` (written by ``mpctl
provision``) in the ``X-Monplat-Token`` header.  Only the standard
library is used, so it runs on the zabbix image's system Python.
Failures are written to stderr, where the Zabbix alerter picks them up,
and end with exit status 1; the token is never written out.
"""
import socket
import sys
import urllib
import urllib2

TIMEOUT = 10
TOKEN_FILE = '/var/lib/monplat/secrets/intake.token'
TOKEN_HEADER = 'X-Monplat-Token'
# The API answers with a short JSON document; read no more than this.
MAX_REPLY = 64 * 1024
USAGE = 'usage: mp_alert.py URL SUBJECT MESSAGE\n'


def read_token(path):
    with open(path) as handle:
        return handle.read().strip()


def build_request(url, subject, message, token):
    data = urllib.urlencode({'subject': subject, 'body': message})
    return urllib2.Request(
        url, data, {'Content-Type': 'application/x-www-form-urlencoded',
                    TOKEN_HEADER: token})


def main(argv):
    if len(argv) != 4:
        sys.stderr.write(USAGE)
        return 2
    url, subject, message = argv[1:]
    try:
        token = read_token(TOKEN_FILE)
    except IOError as exc:
        sys.stderr.write('mp_alert: cannot read the intake token file %s: '
                         '%s\n' % (TOKEN_FILE, exc.strerror))
        return 1
    if not token:
        sys.stderr.write('mp_alert: the intake token file %s is empty\n'
                         % TOKEN_FILE)
        return 1
    try:
        response = urllib2.urlopen(
            build_request(url, subject, message, token), timeout=TIMEOUT)
        response.read(MAX_REPLY)
    except urllib2.HTTPError as exc:
        sys.stderr.write('mp_alert: POST %s failed with HTTP %d: %s\n'
                         % (url, exc.code, exc.read(MAX_REPLY)))
        return 1
    except (urllib2.URLError, socket.error, IOError) as exc:
        sys.stderr.write('mp_alert: POST %s failed: %s\n' % (url, exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
