import datetime
import json

import mock
import psycopg2
import pytest

from monplat import events
from monplat.api.app import create_app

CFG = {'database': {'zabbix_dsn': 'dbname=zabbix',
                    'monplat_dsn': 'dbname=monplat'}}

SUBJECT = 'PROBLEM: High CPU on mp-test-alert'
BODY = ('eventid=1234\n'
        'status=PROBLEM\n'
        'host=mp-test-alert\n'
        'trigger_id=13555\n'
        'trigger_name=High CPU on mp-test-alert\n'
        'severity=4\n'
        'time=2014.12.18 12:34:56\n'
        'value=99 %\n')


def body(**changes):
    lines = []
    for line in BODY.splitlines():
        key = line.split('=', 1)[0]
        if key in changes:
            if changes[key] is None:
                continue
            line = '%s=%s' % (key, changes[key])
        lines.append(line)
    return '\n'.join(lines) + '\n'


# --- parse_alert -------------------------------------------------------------

def test_parse_alert_reads_every_field():
    event = events.parse_alert(SUBJECT, BODY)
    assert event.eventid == 1234
    assert event.status == 'PROBLEM'
    assert event.host == 'mp-test-alert'
    assert event.trigger_id == 13555
    assert event.trigger_name == 'High CPU on mp-test-alert'
    assert event.item_value == '99 %'
    assert event.event_time.utcoffset() == datetime.timedelta(0)
    assert event.event_time.replace(tzinfo=None) == \
        datetime.datetime(2014, 12, 18, 12, 34, 56)


def test_parse_alert_maps_nseverity_to_an_int():
    assert events.parse_alert(SUBJECT, body(severity='4')).severity == 4
    assert events.parse_alert(SUBJECT, body(severity='0')).severity == 0
    assert events.parse_alert(SUBJECT, body(severity=' 5 ')).severity == 5


@pytest.mark.parametrize('bad', ['High', '6', '-1', ''])
def test_parse_alert_rejects_a_severity_outside_0_to_5(bad):
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, body(severity=bad))
    assert 'severity' in str(exc.value)


def test_parse_alert_rejects_a_missing_eventid():
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, body(eventid=None))
    assert 'eventid' in str(exc.value)


def test_parse_alert_rejects_an_unexpanded_macro_as_eventid():
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, body(eventid='{EVENT.ID}'))
    assert 'eventid' in str(exc.value)


def test_parse_alert_rejects_an_unknown_status():
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, body(status='UNKNOWN'))
    assert 'status' in str(exc.value)


def test_parse_alert_accepts_crlf_and_equals_signs_in_values():
    raw = body(value='a=b').replace('\n', '\r\n')
    event = events.parse_alert(SUBJECT, raw)
    assert event.item_value == 'a=b'
    assert event.host == 'mp-test-alert'


def test_parse_alert_reads_an_ok_event():
    event = events.parse_alert('OK: High CPU', body(status='OK'))
    assert event.status == 'OK'


def test_parse_alert_rejects_a_bad_time():
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, body(time='yesterday'))
    assert 'time' in str(exc.value)


def test_value_is_last_and_swallows_injected_lines():
    injected = '99\nstatus=OK\neventid=1\nhost=other'
    event = events.parse_alert(SUBJECT, body(value=injected))
    assert event.item_value == injected
    assert event.status == 'PROBLEM'
    assert event.eventid == 1234
    assert event.host == 'mp-test-alert'


def test_injected_lines_in_a_value_with_crlf_stay_in_the_value():
    raw = body(value='5\r\nstatus=OK').replace('\n', '\r\n')
    event = events.parse_alert(SUBJECT, raw)
    assert event.status == 'PROBLEM'
    assert 'status=OK' in event.item_value


@pytest.mark.parametrize('extra', ['status=OK', 'eventid=1',
                                   'host=mp-test-alert'])
def test_a_duplicate_key_before_value_is_rejected(extra):
    raw = extra + '\n' + BODY
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, raw)
    assert 'duplicate' in str(exc.value)


def test_an_unknown_key_is_rejected():
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, 'colour=red\n' + BODY)
    assert 'unknown' in str(exc.value)


def test_a_line_without_key_and_value_is_rejected():
    raw = BODY.replace('trigger_name=High CPU on mp-test-alert\n',
                       'trigger_name=High CPU\non mp-test-alert\n')
    with pytest.raises(events.EventError):
        events.parse_alert(SUBJECT, raw)


def test_a_body_without_value_line_is_rejected():
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, body(value=None))
    assert 'value' in str(exc.value)


def test_fields_after_value_are_not_read_as_fields():
    # A message in the old order (value before time) has no time field.
    raw = body(time=None) + 'time=2014.12.18 12:34:56\n'
    lines = raw.splitlines()
    assert lines[-2].startswith('value=') and lines[-1].startswith('time=')
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, raw)
    assert 'time' in str(exc.value)


def test_an_oversized_message_is_rejected():
    raw = body(value='x' * (events.MAX_BODY + 1))
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, raw)
    assert 'too large' in str(exc.value)


@pytest.mark.parametrize('field, bad', [
    ('eventid', '+1234'), ('eventid', '-5'), ('eventid', '12 34'),
    ('eventid', '0x10'), ('trigger_id', '13555; drop'),
    ('trigger_id', '-1'), ('status', 'problem'),
    ('host', 'mp-test-alert\tx'), ('host', 'a;b'), ('host', 'a' * 129),
    ('host', ''), ('time', '2014.12.18'),
])
def test_fields_are_strictly_validated(field, bad):
    with pytest.raises(events.EventError) as exc:
        events.parse_alert(SUBJECT, body(**{field: bad}))
    assert field in str(exc.value)


def test_host_names_with_zabbix_host_name_characters_are_accepted():
    for host in ('Zabbix server', 'mp_test.alert-1'):
        assert events.parse_alert(SUBJECT, body(host=host)).host == host


def test_long_values_and_trigger_names_are_cut_to_the_column_size():
    event = events.parse_alert(SUBJECT, body(value='v' * 300,
                                             trigger_name='t' * 300))
    assert event.item_value == 'v' * 255
    assert event.trigger_name == 't' * 255


def test_an_empty_value_is_stored_as_null():
    assert events.parse_alert(SUBJECT, body(value='')).item_value is None


# --- store -------------------------------------------------------------------

class FakeEventsTable(object):
    """A connection whose cursor keeps ``events`` rows in memory, keyed
    on the named ``eventid`` and ``status`` parameters that ``store``
    passes, and enforces the table's UNIQUE (eventid, status)."""

    def __init__(self):
        self.rows = {}
        self.commits = 0
        self.inserts = 0

    def cursor(self):
        return FakeCursor(self)

    def commit(self):
        self.commits += 1

    def rollback(self):
        pass


class FakeCursor(object):
    def __init__(self, table):
        self.table = table
        self.result = None

    def execute(self, sql, params):
        key = (params['eventid'], params['status'])
        verb = sql.split()[0].upper()
        if verb == 'INSERT':
            guarded = 'NOT EXISTS' in sql.upper()
            if key in self.table.rows:
                if not guarded:
                    raise psycopg2.IntegrityError('duplicate key')
                self.result = None
                return
            self.table.inserts += 1
            self.table.rows[key] = len(self.table.rows) + 1
            self.result = (self.table.rows[key],)
        elif verb == 'SELECT':
            found = self.table.rows.get(key)
            self.result = (found,) if found else None
        else:
            raise AssertionError('unexpected SQL: %s' % sql)

    def fetchone(self):
        return self.result

    def close(self):
        pass


def test_store_inserts_a_new_event_and_returns_its_id():
    conn = FakeEventsTable()
    event_id = events.store(conn, events.parse_alert(SUBJECT, BODY))
    assert event_id == 1
    assert conn.inserts == 1
    assert conn.commits == 1


def test_duplicate_eventid_and_status_is_stored_once():
    conn = FakeEventsTable()
    event = events.parse_alert(SUBJECT, BODY)
    first = events.store(conn, event)
    second = events.store(conn, event)
    assert first == second
    assert conn.inserts == 1
    assert len(conn.rows) == 1


def test_problem_and_ok_for_one_eventid_are_two_rows():
    conn = FakeEventsTable()
    problem = events.store(conn, events.parse_alert(SUBJECT, BODY))
    ok = events.store(conn, events.parse_alert(SUBJECT, body(status='OK')))
    assert problem != ok
    assert len(conn.rows) == 2


# --- notification claim ----------------------------------------------------

def _cursor_conn(fetchone):
    conn = mock.MagicMock(name='conn')
    conn.cursor.return_value.fetchone.return_value = fetchone
    return conn


def test_claim_notification_sets_notified_only_if_unset():
    conn = _cursor_conn((7,))
    assert events.claim_notification(conn, 7) is True
    sql, params = conn.cursor.return_value.execute.call_args[0]
    assert sql.startswith('UPDATE events SET notified = true')
    assert 'NOT notified' in sql
    assert params == (7,)
    assert conn.commit.called


def test_claim_notification_is_false_when_already_notified():
    assert events.claim_notification(_cursor_conn(None), 7) is False


def test_release_notification_clears_the_flag():
    conn = _cursor_conn(None)
    events.release_notification(conn, 7)
    sql, params = conn.cursor.return_value.execute.call_args[0]
    assert sql.startswith('UPDATE events SET notified = false')
    assert params == (7,)
    assert conn.commit.called


# --- HTTP API ---------------------------------------------------------------

class TokenClient(object):
    """A Flask test client that sends the intake token on POSTs."""

    def __init__(self, client, headers):
        self.client = client
        self.headers = headers

    def post(self, *args, **kwargs):
        kwargs.setdefault('headers', self.headers)
        return self.client.post(*args, **kwargs)

    def get(self, *args, **kwargs):
        return self.client.get(*args, **kwargs)


@pytest.yield_fixture
def client(intake):
    with mock.patch('monplat.api.app.db.connect') as connect:
        connect.return_value = mock.MagicMock(name='conn')
        app = create_app(intake.cfg(CFG))
        app.testing = True
        yield TokenClient(app.test_client(), intake.headers), connect


def _json(response):
    return json.loads(response.data.decode('utf-8'))


@pytest.yield_fixture
def push():
    with mock.patch('monplat.api.app.notify.push', return_value=True) as push:
        yield push


@pytest.yield_fixture
def claim():
    with mock.patch('monplat.api.app.events.claim_notification',
                    return_value=True) as claim:
        yield claim


@pytest.yield_fixture
def release():
    with mock.patch('monplat.api.app.events.release_notification') as rel:
        yield rel


def test_post_event_from_form_stores_it(client, push, claim, release):
    http, connect = client
    with mock.patch('monplat.api.app.events.store', return_value=7) as store:
        response = http.post('/api/v1/events',
                             data={'subject': SUBJECT, 'body': BODY})
    assert response.status_code == 201
    assert _json(response) == {'id': 7, 'notified': True,
                               'remediation': None}
    assert connect.call_args[0][1] == 'monplat'
    stored = store.call_args[0][1]
    assert (stored.eventid, stored.status, stored.severity) == \
        (1234, 'PROBLEM', 4)


def test_post_event_accepts_json(client, push, claim, release):
    http, _ = client
    with mock.patch('monplat.api.app.events.store', return_value=8):
        response = http.post('/api/v1/events',
                             data=json.dumps({'subject': SUBJECT,
                                              'body': BODY}),
                             content_type='application/json')
    assert response.status_code == 201
    assert _json(response)['id'] == 8


def test_post_event_without_eventid_is_400(client, push):
    http, _ = client
    with mock.patch('monplat.api.app.events.store') as store:
        response = http.post('/api/v1/events',
                             data={'subject': SUBJECT,
                                   'body': body(eventid=None)})
    assert response.status_code == 400
    assert 'eventid' in _json(response)['error']
    assert not store.called
    assert not push.called


def test_post_event_pushes_the_parsed_event_once_claimed(client, push, claim,
                                                         release):
    http, connect = client
    with mock.patch('monplat.api.app.events.store', return_value=7):
        http.post('/api/v1/events', data={'subject': SUBJECT, 'body': BODY})
    assert claim.call_args[0][1] == 7
    cfg, event = push.call_args[0]
    assert cfg['database'] is CFG['database']
    assert (event.eventid, event.status, event.trigger_name) == \
        (1234, 'PROBLEM', 'High CPU on mp-test-alert')
    assert not release.called


def test_failed_push_releases_the_claim_and_reports_not_notified(
        client, push, claim, release):
    http, _ = client
    push.return_value = False
    with mock.patch('monplat.api.app.events.store', return_value=7):
        response = http.post('/api/v1/events',
                             data={'subject': SUBJECT, 'body': BODY})
    assert response.status_code == 201
    assert _json(response)['notified'] is False
    assert release.call_args[0][1] == 7


def test_already_notified_event_is_not_pushed_again(client, push, claim,
                                                    release):
    http, _ = client
    claim.return_value = False
    with mock.patch('monplat.api.app.events.store', return_value=7):
        response = http.post('/api/v1/events',
                             data={'subject': SUBJECT, 'body': BODY})
    assert response.status_code == 201
    assert _json(response)['notified'] is True
    assert not push.called
    assert not release.called


# --- intake authentication -------------------------------------------------

SENTINEL = 'S3NTINEL-intake-token'


@pytest.mark.parametrize('headers', [
    {}, {'X-Monplat-Token': ''}, {'X-Monplat-Token': 'wrong'},
    {'X-Monplat-Token': 'a' * 64},
])
def test_post_event_without_the_right_token_is_401_and_stores_nothing(
        client, push, headers):
    http, connect = client
    with mock.patch('monplat.api.app.events.store') as store:
        response = http.post('/api/v1/events', headers=headers,
                             data={'subject': SUBJECT, 'body': BODY})
    assert response.status_code == 401
    assert 'token' in _json(response)['error'].lower()
    assert not store.called
    assert not push.called
    assert not connect.called


def test_post_event_is_503_when_no_token_is_configured(tmpdir, push):
    cfg = dict(CFG, api={'intake_token_file': str(tmpdir.join('none'))})
    with mock.patch('monplat.api.app.db.connect') as connect, \
            mock.patch('monplat.api.app.events.store') as store:
        app = create_app(cfg)
        response = app.test_client().post(
            '/api/v1/events', headers={'X-Monplat-Token': ''},
            data={'subject': SUBJECT, 'body': BODY})
    assert response.status_code == 503
    assert str(tmpdir) not in response.data.decode('utf-8')
    assert not store.called
    assert not connect.called


def test_intake_errors_never_echo_the_token(tmpdir, push):
    path = tmpdir.join('intake.token')
    path.write(SENTINEL)
    cfg = dict(CFG, api={'intake_token_file': str(path)})
    app = create_app(cfg)
    http = app.test_client()
    with mock.patch('monplat.api.app.db.connect'), \
            mock.patch('monplat.api.app.events.store', return_value=1):
        responses = [
            http.post('/api/v1/events', data={'body': BODY},
                      headers={'X-Monplat-Token': SENTINEL[:-1]}),
            http.post('/api/v1/events', data={'body': BODY}),
            http.post('/api/v1/events', data={'body': body(eventid=None)},
                      headers={'X-Monplat-Token': SENTINEL}),
        ]
    assert [r.status_code for r in responses] == [401, 401, 400]
    for response in responses:
        assert SENTINEL not in response.data.decode('utf-8')


def test_oversized_request_is_413_and_not_stored(client, push):
    http, _ = client
    with mock.patch('monplat.api.app.events.store') as store:
        response = http.post('/api/v1/events', data={
            'subject': SUBJECT, 'body': 'x' * (128 * 1024)})
    assert response.status_code == 413
    assert not store.called


def test_listing_events_needs_no_token(client):
    http, connect = client
    connect.return_value.cursor.return_value.fetchall.return_value = []
    assert http.client.get('/api/v1/events').status_code == 200


def test_post_event_without_body_is_400(client):
    http, _ = client
    response = http.post('/api/v1/events', data={'subject': SUBJECT})
    assert response.status_code == 400


ROW = (3, 1234, 'PROBLEM', 'mp-test-alert', 13555,
       'High CPU on mp-test-alert', 4, '99 %',
       datetime.datetime(2014, 12, 18, 12, 34, 56,
                         tzinfo=psycopg2.tz.FixedOffsetTimezone(0)),
       datetime.datetime(2014, 12, 18, 12, 35, 0,
                         tzinfo=psycopg2.tz.FixedOffsetTimezone(0)),
       False)


def test_get_events_filters_and_orders_newest_first(client):
    http, connect = client
    cursor = connect.return_value.cursor.return_value
    cursor.fetchall.return_value = [ROW]
    response = http.get('/api/v1/events?host=mp-test-alert'
                        '&status=PROBLEM&limit=5')
    assert response.status_code == 200
    assert _json(response) == [{
        'id': 3, 'eventid': 1234, 'status': 'PROBLEM',
        'host': 'mp-test-alert', 'trigger_id': 13555,
        'trigger_name': 'High CPU on mp-test-alert', 'severity': 4,
        'item_value': '99 %', 'event_time': '2014-12-18T12:34:56+00:00',
        'received_at': '2014-12-18T12:35:00+00:00', 'notified': False}]
    sql, params = cursor.execute.call_args[0]
    assert 'host = %s' in sql and 'status = %s' in sql
    assert 'ORDER BY received_at DESC, id DESC' in sql
    assert params == ('mp-test-alert', 'PROBLEM', 5)


def test_get_events_without_filters_uses_the_default_limit(client):
    http, connect = client
    cursor = connect.return_value.cursor.return_value
    cursor.fetchall.return_value = []
    response = http.get('/api/v1/events')
    assert response.status_code == 200
    sql, params = cursor.execute.call_args[0]
    assert 'WHERE' not in sql
    assert params == (events.DEFAULT_LIMIT,)


@pytest.mark.parametrize('query', ['status=BROKEN', 'limit=0', 'limit=x'])
def test_get_events_rejects_bad_arguments(client, query):
    http, _ = client
    response = http.get('/api/v1/events?' + query)
    assert response.status_code == 400
