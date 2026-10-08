import mock
import pytest
import requests
from pyzabbix import ZabbixAPIException

from monplat.zabbix import api

CFG = {'zabbix': {'url': 'http://zabbix/zabbix', 'user': 'Admin',
                  'password': 'zabbix'}}


@pytest.yield_fixture
def no_sleep():
    with mock.patch('monplat.zabbix.api.time.sleep') as sleep:
        yield sleep


def test_connect_logs_in_with_configured_url_and_credentials(no_sleep):
    with mock.patch('monplat.zabbix.api.ZabbixAPI') as cls:
        zapi = api.connect(CFG)
    cls.assert_called_once_with('http://zabbix/zabbix', timeout=mock.ANY)
    assert zapi is cls.return_value
    zapi.login.assert_called_once_with('Admin', 'zabbix')
    assert not no_sleep.called


def test_connect_retries_on_connection_error_then_succeeds(no_sleep):
    with mock.patch('monplat.zabbix.api.ZabbixAPI') as cls:
        cls.return_value.login.side_effect = [
            requests.ConnectionError('refused'),
            requests.ConnectionError('refused'),
            None,
        ]
        zapi = api.connect(CFG, retries=5, delay=2)
    assert zapi.login.call_count == 3
    assert no_sleep.call_args_list == [mock.call(2), mock.call(2)]


def test_connect_retries_while_frontend_returns_non_json(no_sleep):
    with mock.patch('monplat.zabbix.api.ZabbixAPI') as cls:
        cls.return_value.login.side_effect = [
            ZabbixAPIException('Unable to parse json: <html>'),
            None,
        ]
        api.connect(CFG, retries=3)
    assert cls.return_value.login.call_count == 2


def test_connect_gives_up_after_n_attempts(no_sleep):
    with mock.patch('monplat.zabbix.api.ZabbixAPI') as cls:
        cls.return_value.login.side_effect = requests.ConnectionError('down')
        with pytest.raises(api.ZabbixUnavailable) as err:
            api.connect(CFG, retries=4, delay=1)
    assert cls.return_value.login.call_count == 4
    assert no_sleep.call_count == 3
    assert 'http://zabbix/zabbix' in str(err.value)
    assert '4 attempts' in str(err.value)


def test_connect_does_not_retry_api_errors(no_sleep):
    with mock.patch('monplat.zabbix.api.ZabbixAPI') as cls:
        cls.return_value.login.side_effect = ZabbixAPIException(
            'Error -32602: Invalid params., Login name or password is '
            'incorrect.')
        with pytest.raises(ZabbixAPIException):
            api.connect(CFG, retries=5)
    assert cls.return_value.login.call_count == 1


def test_get_or_create_returns_existing_id_without_create():
    zapi = mock.MagicMock()
    zapi.hostgroup.get.return_value = [{'groupid': '7', 'name': 'MP'}]
    result = api.get_or_create(zapi, 'hostgroup', {'name': 'MP'},
                               {'name': 'MP'})
    assert result == ('7', False)
    zapi.hostgroup.get.assert_called_once_with(filter={'name': 'MP'},
                                               output='extend')
    assert not zapi.hostgroup.create.called


def test_get_or_create_creates_when_missing():
    zapi = mock.MagicMock()
    zapi.host.get.return_value = []
    zapi.host.create.return_value = {'hostids': ['10105']}
    params = {'host': 'mp-collector', 'groups': [{'groupid': '7'}]}
    result = api.get_or_create(zapi, 'host', {'host': 'mp-collector'},
                               params)
    assert result == ('10105', True)
    zapi.host.create.assert_called_once_with(**params)


def test_get_or_create_uses_object_specific_id_fields():
    zapi = mock.MagicMock()
    zapi.usergroup.get.return_value = [{'usrgrpid': '13'}]
    zapi.mediatype.get.return_value = []
    zapi.mediatype.create.return_value = {'mediatypeids': ['4']}
    assert api.get_or_create(zapi, 'usergroup', {'name': 'x'}, {}) == \
        ('13', False)
    assert api.get_or_create(zapi, 'mediatype', {'description': 'y'},
                             {'description': 'y'}) == ('4', True)
