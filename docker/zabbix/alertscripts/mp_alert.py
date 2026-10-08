#!/usr/bin/env python
"""Zabbix alertscript for the "MP API" media type.

Zabbix runs it as ``mp_alert.py {ALERT.SENDTO} {ALERT.SUBJECT}
{ALERT.MESSAGE}``.  The send-to address of the user media is the API's
``/api/v1/events`` URL; the subject and message are posted there as a
form.  Only the standard library is used, so it runs on the zabbix
image's system Python.  Failures are written to stderr, where the Zabbix
alerter picks them up, and end with exit status 1.
"""
import socket
import sys
import urllib
import urllib2

TIMEOUT = 10
USAGE = 'usage: mp_alert.py URL SUBJECT MESSAGE\n'


def build_request(url, subject, message):
    data = urllib.urlencode({'subject': subject, 'body': message})
    return urllib2.Request(
        url, data, {'Content-Type': 'application/x-www-form-urlencoded'})


def main(argv):
    if len(argv) != 4:
        sys.stderr.write(USAGE)
        return 2
    url, subject, message = argv[1:]
    try:
        response = urllib2.urlopen(build_request(url, subject, message),
                                   timeout=TIMEOUT)
        response.read()
    except urllib2.HTTPError as exc:
        sys.stderr.write('mp_alert: POST %s failed with HTTP %d: %s\n'
                         % (url, exc.code, exc.read()))
        return 1
    except (urllib2.URLError, socket.error) as exc:
        sys.stderr.write('mp_alert: POST %s failed: %s\n' % (url, exc))
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
