"""Provision the Zabbix media type, user media and action that send alerts
to the API (``config/actions.yml``), and the global scripts that the
remediation rules run (``config/remediation.yml``).

``plan()`` returns ``templates.Change`` objects in the same form as the
template plan, and only calls ``*.get``; ``templates.apply()`` performs
them.  Objects are matched by name (media type description, user alias,
action name, script name), so a second run reports everything
unchanged.
"""
import os

import yaml

from monplat.templates import Change, Ref, SpecError

DEFAULT_PATH = os.path.join('config', 'actions.yml')

EVENTSOURCE_TRIGGERS = 0
OPERATION_SEND_MESSAGE = 0
STATUS_ENABLED = 0
SCRIPT_TYPE_CUSTOM = 0
EXECUTE_ON_AGENT = 0
# Only users with write access to a host may run the script on it.
HOST_ACCESS_WRITE = 3

_SECTIONS = {
    'media_type': ('description', 'type', 'exec_path'),
    'user_media': ('user', 'sendto', 'severity', 'period'),
    'action': ('name', 'esc_period', 'recovery_msg', 'subject',
               'recovery_subject', 'message', 'conditions'),
}

# Action fields compared with what Zabbix reports.
_ACTION_FIELDS = ('status', 'esc_period', 'def_shortdata', 'def_longdata',
                  'recovery_msg', 'r_shortdata', 'r_longdata')


def load(path=DEFAULT_PATH):
    """Read and check the actions file; raises ``SpecError``."""
    try:
        with open(path) as handle:
            data = yaml.safe_load(handle)
    except (IOError, yaml.YAMLError) as exc:
        raise SpecError('%s: %s' % (path, exc))
    if not isinstance(data, dict):
        raise SpecError('%s: must be a mapping' % path)
    for section, fields in sorted(_SECTIONS.items()):
        values = data.get(section)
        if not isinstance(values, dict):
            raise SpecError('%s: missing %r section' % (path, section))
        for field in fields:
            if values.get(field) in (None, ''):
                raise SpecError('%s: %s needs %r' % (path, section, field))
    return data


def _text(value):
    return str(value).replace('\r\n', '\n')


def _conditions(spec):
    return [{'conditiontype': int(c['conditiontype']),
             'operator': int(c['operator']),
             'value': str(c['value'])}
            for c in spec['action']['conditions']]


def _condition_set(conditions):
    return sorted((str(c['conditiontype']), str(c['operator']),
                   str(c['value'])) for c in conditions or [])


def _action_fields(spec):
    action = spec['action']
    return {
        'status': STATUS_ENABLED,
        'esc_period': int(action['esc_period']),
        'def_shortdata': action['subject'],
        'def_longdata': action['message'],
        'recovery_msg': int(action['recovery_msg']),
        'r_shortdata': action['recovery_subject'],
        'r_longdata': action['message'],
    }


def _plan_media_type(zapi, spec):
    mt = spec['media_type']
    name = mt['description']
    desired = {'description': name, 'type': int(mt['type']),
               'exec_path': mt['exec_path'], 'status': STATUS_ENABLED}
    found = zapi.mediatype.get(filter={'description': name},
                               output='extend')
    if not found:
        return Change('create', 'mediatype', name, 'mediatype.create',
                      desired, None)
    current = found[0]
    diff = dict((k, v) for k, v in desired.items()
                if k in current and str(current[k]) != str(v))
    if diff:
        diff['mediatypeid'] = current['mediatypeid']
        return Change('update', 'mediatype', name, 'mediatype.update', diff,
                      current['mediatypeid'])
    return Change('unchanged', 'mediatype', name, None, None,
                  current['mediatypeid'])


def _plan_user_media(zapi, spec, mediatypeid):
    media = spec['user_media']
    alias = media['user']
    mtname = spec['media_type']['description']
    name = '%s -> %s' % (alias, mtname)
    found = zapi.user.get(filter={'alias': alias},
                          output=['userid', 'alias'], selectMedias='extend')
    if not found:
        raise SpecError('Zabbix user %r does not exist' % alias)
    user = found[0]
    userid = user['userid']
    for existing in user.get('medias') or []:
        if (mediatypeid is not None and
                str(existing['mediatypeid']) == str(mediatypeid) and
                existing['sendto'] == media['sendto']):
            return userid, Change('unchanged', 'media', name, None, None,
                                  existing.get('mediaid'))
    params = {
        'users': [{'userid': userid}],
        'medias': [{'mediatypeid': Ref('mediatype', mtname),
                    'sendto': media['sendto'],
                    'active': 0,
                    'severity': int(media['severity']),
                    'period': media['period']}],
    }
    return userid, Change('create', 'media', name, 'user.addmedia', params,
                          None)


def _plan_action(zapi, spec, userid):
    action = spec['action']
    name = action['name']
    mtname = spec['media_type']['description']
    fields = _action_fields(spec)
    conditions = _conditions(spec)
    found = zapi.action.get(filter={'name': name}, output='extend',
                            selectFilter='extend')
    if not found:
        params = dict(fields)
        params.update({
            'name': name,
            'eventsource': EVENTSOURCE_TRIGGERS,
            'filter': {'evaltype': 0, 'conditions': conditions},
            'operations': [{
                'operationtype': OPERATION_SEND_MESSAGE,
                'esc_step_from': 1,
                'esc_step_to': 1,
                'opmessage': {'default_msg': 1,
                              'mediatypeid': Ref('mediatype', mtname)},
                'opmessage_usr': [{'userid': userid}],
            }],
        })
        return Change('create', 'action', name, 'action.create', params,
                      None)
    current = found[0]
    diff = dict((k, v) for k, v in fields.items()
                if _text(current.get(k, '')) != _text(v))
    current_filter = current.get('filter') or {}
    if (_condition_set(current_filter.get('conditions')) !=
            _condition_set(conditions)):
        diff['filter'] = {'evaltype': 0, 'conditions': conditions}
    if diff:
        diff['actionid'] = current['actionid']
        return Change('update', 'action', name, 'action.update', diff,
                      current['actionid'])
    return Change('unchanged', 'action', name, None, None,
                  current['actionid'])


def plan(zapi, spec):
    """Return the changes for the media type, the user media and the
    action, in that order."""
    media_type = _plan_media_type(zapi, spec)
    userid, media = _plan_user_media(zapi, spec, media_type.id)
    return [media_type, media, _plan_action(zapi, spec, userid)]


def plan_scripts(zapi, rules):
    """Return one change per global script named by the enabled
    remediation ``rules``: a custom script run on the agent with the rule's
    command.  Disabled rules get no script."""
    changes = []
    seen = set()
    for rule in rules:
        if not rule.enabled:
            continue
        name = rule.script
        if name in seen:
            continue
        seen.add(name)
        desired = {'command': rule.command, 'type': SCRIPT_TYPE_CUSTOM,
                   'execute_on': EXECUTE_ON_AGENT,
                   'host_access': HOST_ACCESS_WRITE}
        found = zapi.script.get(filter={'name': name}, output='extend')
        if not found:
            params = dict(desired, name=name)
            changes.append(Change('create', 'script', name, 'script.create',
                                  params, None))
            continue
        current = found[0]
        diff = dict((k, v) for k, v in desired.items()
                    if str(current.get(k, '')) != str(v))
        if diff:
            diff['scriptid'] = current['scriptid']
            changes.append(Change('update', 'script', name, 'script.update',
                                  diff, current['scriptid']))
        else:
            changes.append(Change('unchanged', 'script', name, None, None,
                                  current['scriptid']))
    return changes
