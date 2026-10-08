import os

import mock
import pytest

from monplat import actions, templates

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
ACTIONS_FILE = os.path.join(ROOT, 'config', 'actions.yml')

SPEC_MESSAGE = ('eventid={EVENT.ID}\n'
                'status={TRIGGER.STATUS}\n'
                'host={HOST.HOST}\n'
                'trigger_id={TRIGGER.ID}\n'
                'trigger_name={TRIGGER.NAME}\n'
                'severity={TRIGGER.NSEVERITY}\n'
                'time={EVENT.DATE} {EVENT.TIME}\n'
                'value={ITEM.VALUE}\n')

WRITE_METHODS = ('create', 'update', 'delete', 'addmedia', 'updatemedia',
                 'deletemedia')


def write_calls(zapi):
    return [c for c in zapi.mock_calls
            if c[0].split('.')[-1] in WRITE_METHODS]


@pytest.fixture
def spec():
    return actions.load(ACTIONS_FILE)


def empty_zabbix():
    """No MP media type or action yet; Admin has no media."""
    zapi = mock.MagicMock()
    zapi.mediatype.get.return_value = []
    zapi.action.get.return_value = []
    zapi.user.get.return_value = [{'userid': '1', 'alias': 'Admin',
                                   'medias': []}]
    zapi.mediatype.create.return_value = {'mediatypeids': ['4']}
    zapi.action.create.return_value = {'actionids': ['7']}
    zapi.user.addmedia.return_value = {'mediaids': ['2']}
    return zapi


def provisioned_zabbix(spec):
    zapi = mock.MagicMock()
    mt = spec['media_type']
    zapi.mediatype.get.return_value = [{
        'mediatypeid': '4', 'description': mt['description'],
        'type': str(mt['type']), 'exec_path': mt['exec_path']}]
    media = spec['user_media']
    zapi.user.get.return_value = [{
        'userid': '1', 'alias': 'Admin',
        'medias': [{'mediaid': '2', 'mediatypeid': '4',
                    'sendto': media['sendto'], 'active': '0',
                    'severity': '63', 'period': media['period']}]}]
    act = spec['action']
    zapi.action.get.return_value = [{
        'actionid': '7', 'name': act['name'], 'status': '0',
        'esc_period': str(act['esc_period']),
        'def_shortdata': act['subject'],
        # Zabbix keeps what it was sent; the frontend stores CRLF.
        'def_longdata': act['message'].replace('\n', '\r\n'),
        'recovery_msg': str(act['recovery_msg']),
        'r_shortdata': act['recovery_subject'],
        'r_longdata': act['message'],
        'filter': {'evaltype': '0', 'conditions': [
            {'conditiontype': '5', 'operator': '0', 'value': '1'}]}}]
    return zapi


def summary(changes):
    return [(c.action, c.obj, c.name) for c in changes]


def test_shipped_message_template_matches_the_alertscript_contract(spec):
    assert spec['action']['message'] == SPEC_MESSAGE
    assert spec['media_type']['description'] == 'MP API'
    assert spec['media_type']['exec_path'] == 'mp_alert.py'
    assert spec['action']['name'] == 'MP notify API'
    assert spec['user_media']['user'] == 'Admin'


def test_plan_on_empty_zabbix_creates_media_type_media_and_action(spec):
    zapi = empty_zabbix()
    changes = actions.plan(zapi, spec)
    assert summary(changes) == [
        ('create', 'mediatype', 'MP API'),
        ('create', 'media', 'Admin -> MP API'),
        ('create', 'action', 'MP notify API'),
    ]
    assert write_calls(zapi) == []


def test_provisioned_action_has_problem_condition_and_recovery_msg(spec):
    zapi = empty_zabbix()
    templates.apply(zapi, actions.plan(zapi, spec))
    params = zapi.action.create.call_args[1]
    assert params['name'] == 'MP notify API'
    assert params['eventsource'] == 0
    assert params['recovery_msg'] == 1
    assert params['filter']['evaltype'] == 0
    assert params['filter']['conditions'] == [
        {'conditiontype': 5, 'operator': 0, 'value': '1'}]
    assert params['def_longdata'] == SPEC_MESSAGE
    assert params['r_longdata'] == SPEC_MESSAGE
    operation, = params['operations']
    assert operation['operationtype'] == 0
    assert operation['opmessage'] == {'default_msg': 1, 'mediatypeid': '4'}
    assert operation['opmessage_usr'] == [{'userid': '1'}]
    assert (operation['esc_step_from'], operation['esc_step_to']) == (1, 1)


def test_media_type_runs_the_alertscript(spec):
    zapi = empty_zabbix()
    templates.apply(zapi, actions.plan(zapi, spec))
    params = zapi.mediatype.create.call_args[1]
    assert params == {'description': 'MP API', 'type': 1,
                      'exec_path': 'mp_alert.py', 'status': 0}


def test_admin_media_points_at_the_api_events_endpoint(spec):
    zapi = empty_zabbix()
    templates.apply(zapi, actions.plan(zapi, spec))
    params = zapi.user.addmedia.call_args[1]
    assert params['users'] == [{'userid': '1'}]
    media, = params['medias']
    assert media['mediatypeid'] == '4'
    assert media['sendto'] == 'http://api:5000/api/v1/events'
    assert media['severity'] == 63
    assert media['active'] == 0


def test_plan_on_provisioned_zabbix_is_unchanged(spec):
    zapi = provisioned_zabbix(spec)
    changes = actions.plan(zapi, spec)
    assert set(c.action for c in changes) == set(['unchanged'])
    templates.apply(zapi, changes)
    assert write_calls(zapi) == []


def test_changed_message_updates_the_action(spec):
    zapi = provisioned_zabbix(spec)
    zapi.action.get.return_value[0]['def_longdata'] = 'eventid={EVENT.ID}'
    changes = actions.plan(zapi, spec)
    assert ('update', 'action', 'MP notify API') in summary(changes)
    templates.apply(zapi, changes)
    params = zapi.action.update.call_args[1]
    assert params['actionid'] == '7'
    assert params['def_longdata'] == SPEC_MESSAGE


def test_missing_condition_is_restored_by_update(spec):
    zapi = provisioned_zabbix(spec)
    zapi.action.get.return_value[0]['filter']['conditions'] = []
    templates.apply(zapi, actions.plan(zapi, spec))
    params = zapi.action.update.call_args[1]
    assert params['filter']['conditions'] == [
        {'conditiontype': 5, 'operator': 0, 'value': '1'}]


def test_media_with_other_sendto_gets_a_new_media(spec):
    zapi = provisioned_zabbix(spec)
    zapi.user.get.return_value[0]['medias'][0]['sendto'] = 'http://old/'
    changes = actions.plan(zapi, spec)
    assert ('create', 'media', 'Admin -> MP API') in summary(changes)


def test_unknown_user_is_an_error(spec):
    zapi = empty_zabbix()
    zapi.user.get.return_value = []
    with pytest.raises(templates.SpecError) as exc:
        actions.plan(zapi, spec)
    assert 'Admin' in str(exc.value)


def test_load_rejects_a_file_without_an_action(tmpdir):
    path = tmpdir.join('actions.yml')
    path.write('media_type: {description: X, type: 1, exec_path: x}\n')
    with pytest.raises(templates.SpecError) as exc:
        actions.load(str(path))
    assert 'action' in str(exc.value)
