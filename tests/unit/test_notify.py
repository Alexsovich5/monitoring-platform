import datetime

import mock
import pytest
import requests
from psycopg2.tz import FixedOffsetTimezone

from monplat import events, notify

CFG = {'notify': {'pushover_url': 'http://push.example/1/messages.json',
                  'token': 'app-token', 'user': 'user-key'}}

EVENT = events.Event(
    eventid=1234, status='PROBLEM', host='mp-test-alert', trigger_id=13555,
    trigger_name='High CPU on mp-test-alert', severity=4, item_value='99 %',
    event_time=datetime.datetime(2014, 12, 18, 12, 34, 56,
                                 tzinfo=FixedOffsetTimezone(offset=0)))


@pytest.mark.parametrize('severity,expected', [
    (0, -1), (1, -1), (2, 0), (3, 0), (4, 1), (5, 1)])
def test_priority_maps_zabbix_severity(severity, expected):
    assert notify.priority(severity) == expected


@pytest.mark.parametrize('bad', [-1, 6])
def test_priority_rejects_an_unknown_severity(bad):
    with pytest.raises(ValueError):
        notify.priority(bad)


def _response(status, body='{"status":1}'):
    response = mock.Mock(spec=requests.Response)
    response.status_code = status
    response.text = body
    return response


@pytest.yield_fixture
def post():
    with mock.patch('monplat.notify.requests.post') as post:
        post.return_value = _response(200)
        yield post


def test_push_sends_token_user_title_message_and_priority(post):
    assert notify.push(CFG, EVENT) is True
    assert post.call_count == 1
    args, kwargs = post.call_args
    assert args[0] == 'http://push.example/1/messages.json'
    data = kwargs['data']
    assert data['token'] == 'app-token'
    assert data['user'] == 'user-key'
    assert data['title'] == 'PROBLEM: High CPU on mp-test-alert'
    assert 'mp-test-alert' in data['message']
    assert '99 %' in data['message']
    assert data['priority'] == 1
    assert kwargs['timeout'] > 0


def test_push_uses_the_ok_status_and_low_severity_priority(post):
    notify.push(CFG, EVENT._replace(status='OK', severity=1))
    data = post.call_args[1]['data']
    assert data['title'] == 'OK: High CPU on mp-test-alert'
    assert data['priority'] == -1


def test_non_200_response_returns_false(post):
    post.return_value = _response(400, '{"status":0}')
    assert notify.push(CFG, EVENT) is False


def test_connection_error_returns_false(post):
    post.side_effect = requests.ConnectionError('refused')
    assert notify.push(CFG, EVENT) is False


def test_missing_notify_section_returns_false_without_a_request(post):
    assert notify.push({}, EVENT) is False
    assert not post.called
