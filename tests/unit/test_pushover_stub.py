import json

import pytest

from tests.stubs import pushover_stub


@pytest.fixture
def http():
    app = pushover_stub.create_app()
    app.testing = True
    return app.test_client()


def _json(response):
    return json.loads(response.data.decode('utf-8'))


MESSAGE = {'token': 'stub-app-token', 'user': 'stub-user-key',
           'title': 'PROBLEM: High CPU', 'message': 'host=mp-test-alert',
           'priority': '1'}


def test_records_a_message(http):
    response = http.post('/1/messages.json', data=MESSAGE)
    assert response.status_code == 200
    assert _json(response)['status'] == 1
    received = _json(http.get('/_received'))
    assert len(received) == 1
    assert received[0]['title'] == 'PROBLEM: High CPU'
    assert received[0]['priority'] == 1
    assert received[0]['token'] == 'stub-app-token'


def test_missing_token_is_rejected_with_400(http):
    data = dict(MESSAGE)
    del data['token']
    response = http.post('/1/messages.json', data=data)
    assert response.status_code == 400
    body = _json(response)
    assert body['status'] == 0
    assert any('token' in error for error in body['errors'])
    assert _json(http.get('/_received')) == []


@pytest.mark.parametrize('field', ['user', 'message'])
def test_other_required_fields_are_checked(http, field):
    data = dict(MESSAGE)
    del data[field]
    assert http.post('/1/messages.json', data=data).status_code == 400


def test_delete_clears_received_messages(http):
    http.post('/1/messages.json', data=MESSAGE)
    assert http.delete('/_received').status_code == 204
    assert _json(http.get('/_received')) == []


def test_each_app_has_its_own_message_list():
    first = pushover_stub.create_app().test_client()
    first.post('/1/messages.json', data=MESSAGE)
    second = pushover_stub.create_app().test_client()
    assert _json(second.get('/_received')) == []
