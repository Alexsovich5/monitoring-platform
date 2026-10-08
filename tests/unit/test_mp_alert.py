import StringIO
import imp
import os
import urllib2
import urlparse

import mock
import pytest

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
SCRIPT = os.path.join(ROOT, 'docker', 'zabbix', 'alertscripts', 'mp_alert.py')

URL = 'http://api:5000/api/v1/events'
SUBJECT = 'PROBLEM: High CPU on mp-test-alert'
MESSAGE = 'eventid=1234\nstatus=PROBLEM\nhost=mp-test-alert\n'


SENTINEL = 'S3NTINEL-intake-token'


@pytest.fixture
def mp_alert(tmpdir):
    module = imp.load_source('mp_alert', SCRIPT)
    token_file = tmpdir.join('intake.token')
    token_file.write(SENTINEL + '\n')
    module.TOKEN_FILE = str(token_file)
    return module


def test_script_is_executable_and_uses_only_the_stdlib():
    assert os.access(SCRIPT, os.X_OK)
    with open(SCRIPT) as handle:
        source = handle.read()
    assert source.startswith('#!/usr/bin/env python')
    for module in ('requests', 'monplat', 'yaml'):
        assert 'import %s' % module not in source


def test_posts_subject_and_body_to_the_sendto_url(mp_alert):
    with mock.patch.object(mp_alert.urllib2, 'urlopen') as urlopen:
        urlopen.return_value.read.return_value = '{"id": 1}'
        code = mp_alert.main(['mp_alert.py', URL, SUBJECT, MESSAGE])
    assert code == 0
    request = urlopen.call_args[0][0]
    assert isinstance(request, urllib2.Request)
    assert request.get_full_url() == URL
    assert request.get_method() == 'POST'
    assert request.get_header('Content-type') == \
        'application/x-www-form-urlencoded'
    assert urlparse.parse_qs(request.get_data()) == {
        'subject': [SUBJECT], 'body': [MESSAGE]}
    assert urlopen.call_args[1]['timeout'] == mp_alert.TIMEOUT
    assert request.get_header('X-monplat-token') == SENTINEL


def test_reads_at_most_a_bounded_reply(mp_alert):
    with mock.patch.object(mp_alert.urllib2, 'urlopen') as urlopen:
        urlopen.return_value.read.return_value = '{"id": 1}'
        assert mp_alert.main(['mp_alert.py', URL, SUBJECT, MESSAGE]) == 0
    urlopen.return_value.read.assert_called_once_with(mp_alert.MAX_REPLY)


def test_missing_token_file_fails_without_posting(mp_alert, tmpdir, capsys):
    mp_alert.TOKEN_FILE = str(tmpdir.join('missing'))
    with mock.patch.object(mp_alert.urllib2, 'urlopen') as urlopen:
        code = mp_alert.main(['mp_alert.py', URL, SUBJECT, MESSAGE])
    assert code == 1
    assert not urlopen.called
    _, err = capsys.readouterr()
    assert 'token' in err


@pytest.mark.parametrize('error', [
    urllib2.URLError('refused'),
    urllib2.HTTPError(URL, 401, 'UNAUTHORIZED', {},
                      StringIO.StringIO('{"error": "wrong token"}')),
    IOError('timed out'),
])
def test_failures_never_print_the_token(mp_alert, capsys, error):
    with mock.patch.object(mp_alert.urllib2, 'urlopen', side_effect=error):
        code = mp_alert.main(['mp_alert.py', URL, SUBJECT, MESSAGE])
    assert code == 1
    out, err = capsys.readouterr()
    assert SENTINEL not in out + err


def test_logs_to_stderr_and_exits_1_when_the_api_is_down(mp_alert, capsys):
    with mock.patch.object(mp_alert.urllib2, 'urlopen',
                           side_effect=urllib2.URLError('refused')):
        code = mp_alert.main(['mp_alert.py', URL, SUBJECT, MESSAGE])
    assert code == 1
    _, err = capsys.readouterr()
    assert 'refused' in err and URL in err


def test_logs_the_api_error_body_on_http_error(mp_alert, capsys):
    error = urllib2.HTTPError(URL, 400, 'BAD REQUEST', {},
                              StringIO.StringIO('{"error": "eventid missing"}'))
    with mock.patch.object(mp_alert.urllib2, 'urlopen', side_effect=error):
        code = mp_alert.main(['mp_alert.py', URL, SUBJECT, MESSAGE])
    assert code == 1
    _, err = capsys.readouterr()
    assert '400' in err and 'eventid missing' in err


def test_wrong_argument_count_prints_usage(mp_alert, capsys):
    with mock.patch.object(mp_alert.urllib2, 'urlopen') as urlopen:
        code = mp_alert.main(['mp_alert.py', URL])
    assert code == 2
    assert not urlopen.called
    _, err = capsys.readouterr()
    assert 'usage' in err.lower()
