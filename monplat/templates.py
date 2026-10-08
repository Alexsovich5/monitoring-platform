"""Provision Zabbix templates, items, triggers and hosts from YAML specs.

A spec file (``config/templates/*.yml``) describes one template::

    template: Template MP Linux
    group: MP Templates
    applications: [CPU, ...]
    items:    [{key, name, type, value_type, units, application}, ...]
    triggers: [{name, expression, severity}, ...]
    hosts:    [{host, groups: [...], interfaces: []}, ...]

``validate()`` checks a spec without talking to Zabbix.  ``plan()`` compares
a spec with what the API reports and returns a list of ``Change`` objects
(``create``, ``update`` or ``unchanged``); it only calls ``*.get``.
``apply()`` performs the ``create``/``update`` changes in order.  Objects
that do not exist yet are referred to with ``Ref`` placeholders, which
``apply()`` replaces with the ids returned by earlier ``create`` calls.

Templates are linked to hosts with ``host.massadd``, which adds to the
host's existing templates instead of replacing them.
"""
import collections
import glob
import os
import re

import yaml

from monplat.zabbix import api

DEFAULT_DIR = os.path.join('config', 'templates')

ITEM_TYPES = {'agent': 0, 'trapper': 2, 'snmpv2': 4}
VALUE_TYPES = {'float': 0, 'unsigned': 3}
SEVERITIES = {'not_classified': 0, 'information': 1, 'warning': 2,
              'average': 3, 'high': 4, 'disaster': 5}
# Host interface types that hosts in a spec may declare.
INTERFACE_TYPES = {}

DEFAULT_DELAY = 60

# Zabbix 2.4's host.create refuses a host without interfaces ("No
# interfaces for host").  A host declared with ``interfaces: []`` only has
# trapper items, so it is given this agent interface, which none of its
# items poll.
TRAPPER_ONLY_INTERFACE = {'type': 1, 'main': 1, 'useip': 1,
                          'ip': '127.0.0.1', 'dns': '', 'port': '10050'}

# {<host>:<key>.<function>(<args>)}
_FUNCTION_REF = re.compile(r'\{([^:{}]+):(.+?)\.(\w+)\(([^()]*)\)\}')


class SpecError(Exception):
    pass


class Spec(dict):
    """A template spec as loaded from YAML; ``path`` is its source file."""

    def __init__(self, data, path=None):
        dict.__init__(self, data)
        self.path = path


class Ref(object):
    """The id of an object that ``apply()`` resolves at run time."""

    def __init__(self, obj, name):
        self.obj = obj
        self.name = name

    def __eq__(self, other):
        return (isinstance(other, Ref) and
                (self.obj, self.name) == (other.obj, other.name))

    def __ne__(self, other):
        return not self == other

    def __repr__(self):
        return 'Ref(%r, %r)' % (self.obj, self.name)


Change = collections.namedtuple('Change',
                                'action obj name method params id')

ACTIONS = ('create', 'update', 'unchanged')


# --- loading and validation -------------------------------------------------

def load_specs(directory=DEFAULT_DIR):
    """Return the specs in ``directory/*.yml``, sorted by file name."""
    specs = []
    for path in sorted(glob.glob(os.path.join(directory, '*.yml'))):
        try:
            with open(path) as handle:
                data = yaml.safe_load(handle)
        except yaml.YAMLError as exc:
            raise SpecError('%s: invalid YAML: %s' % (path, exc))
        if not isinstance(data, dict):
            raise SpecError('%s: a template spec must be a mapping' % path)
        specs.append(Spec(data, path))
    return specs


def _require(mapping, field, where):
    value = mapping.get(field)
    if value is None or value == '':
        raise SpecError('%s: missing %r' % (where, field))
    return value


def _list(spec, field, where):
    value = spec.get(field) or []
    if not isinstance(value, list):
        raise SpecError('%s: %r must be a list' % (where, field))
    return value


def trigger_references(expression):
    """Return ``[(host, key, function)]`` for each ``{host:key.func()}``."""
    return [(m.group(1), m.group(2), m.group(3))
            for m in _FUNCTION_REF.finditer(expression)]


def validate(spec):
    """Raise ``SpecError`` when ``spec`` cannot be provisioned."""
    where = getattr(spec, 'path', None) or 'spec'
    name = _require(spec, 'template', where)
    where = '%s (%s)' % (where, name)
    _require(spec, 'group', where)
    applications = _list(spec, 'applications', where)

    keys = set()
    for index, item in enumerate(_list(spec, 'items', where)):
        label = '%s: item %d' % (where, index + 1)
        key = _require(item, 'key', label)
        label = '%s: item %r' % (where, key)
        if key in keys:
            raise SpecError('%s: duplicate key' % label)
        keys.add(key)
        _require(item, 'name', label)
        itype = _require(item, 'type', label)
        if itype not in ITEM_TYPES:
            raise SpecError('%s: unknown type %r (expected one of %s)'
                            % (label, itype, ', '.join(sorted(ITEM_TYPES))))
        vtype = _require(item, 'value_type', label)
        if vtype not in VALUE_TYPES:
            raise SpecError('%s: unknown value_type %r' % (label, vtype))
        if itype == 'snmpv2':
            _require(item, 'snmp_oid', label)
            _require(item, 'snmp_community', label)
        app = item.get('application')
        if app is not None and app not in applications:
            raise SpecError('%s: application %r is not listed in '
                            'applications' % (label, app))

    for index, trigger in enumerate(_list(spec, 'triggers', where)):
        label = '%s: trigger %d' % (where, index + 1)
        tname = _require(trigger, 'name', label)
        label = '%s: trigger %r' % (where, tname)
        expression = _require(trigger, 'expression', label)
        severity = _require(trigger, 'severity', label)
        if severity not in SEVERITIES:
            raise SpecError('%s: unknown severity %r' % (label, severity))
        refs = trigger_references(expression)
        if not refs:
            raise SpecError('%s: expression references no item' % label)
        for host, key, _ in refs:
            if host != name:
                raise SpecError('%s: expression references host %r, not '
                                'the template' % (label, host))
            if key not in keys:
                raise SpecError('%s: expression references undefined key '
                                '%r' % (label, key))

    for index, host in enumerate(_list(spec, 'hosts', where)):
        label = '%s: host %d' % (where, index + 1)
        hname = _require(host, 'host', label)
        label = '%s: host %r' % (where, hname)
        groups = host.get('groups')
        if not groups or not isinstance(groups, list):
            raise SpecError('%s: needs a non-empty list of groups' % label)
        interfaces = host.get('interfaces') or []
        if not isinstance(interfaces, list):
            raise SpecError('%s: interfaces must be a list' % label)
        for interface in interfaces:
            itype = (interface or {}).get('type')
            if itype not in INTERFACE_TYPES:
                raise SpecError('%s: unsupported interface type %r'
                                % (label, itype))


# --- planning ---------------------------------------------------------------

def _item_params(item):
    itype = item['type']
    params = {
        'key_': item['key'],
        'name': item['name'],
        'type': ITEM_TYPES[itype],
        'value_type': VALUE_TYPES[item['value_type']],
        'units': item.get('units') or '',
    }
    if itype != 'trapper':
        params['delay'] = int(item.get('delay', DEFAULT_DELAY))
    return params


def _normalise(expression):
    return re.sub(r'\s+', '', expression)


def _plan_groups(zapi, names):
    changes = []
    for name in names:
        found = zapi.hostgroup.get(filter={'name': name},
                                   output=['groupid', 'name'])
        if found:
            changes.append(Change('unchanged', 'hostgroup', name, None, None,
                                  found[0]['groupid']))
        else:
            changes.append(Change('create', 'hostgroup', name,
                                  'hostgroup.create', {'name': name}, None))
    return changes


def _plan_items(spec, existing, tref):
    by_key = dict((i['key_'], i) for i in existing)
    changes = []
    for item in spec.get('items') or []:
        desired = _item_params(item)
        app = item.get('application')
        apps = [Ref('application', app)] if app else []
        current = by_key.get(item['key'])
        if current is None:
            params = dict(desired, hostid=tref, applications=apps)
            changes.append(Change('create', 'item', item['key'],
                                  'item.create', params, None))
            continue
        diff = dict((f, v) for f, v in desired.items()
                    if f != 'key_' and str(current.get(f, '')) != str(v))
        current_apps = set(a['name'] for a in current.get('applications')
                           or [])
        if current_apps != set([app] if app else []):
            diff['applications'] = apps
        if diff:
            diff['itemid'] = current['itemid']
            changes.append(Change('update', 'item', item['key'],
                                  'item.update', diff, current['itemid']))
        else:
            changes.append(Change('unchanged', 'item', item['key'], None,
                                  None, current['itemid']))
    return changes


def _plan_triggers(spec, existing):
    by_name = dict((t['description'], t) for t in existing)
    changes = []
    for trigger in spec.get('triggers') or []:
        name = trigger['name']
        priority = SEVERITIES[trigger['severity']]
        current = by_name.get(name)
        if current is None:
            params = {'description': name,
                      'expression': trigger['expression'],
                      'priority': priority}
            changes.append(Change('create', 'trigger', name,
                                  'trigger.create', params, None))
        elif (_normalise(current['expression']) !=
              _normalise(trigger['expression']) or
              str(current['priority']) != str(priority)):
            params = {'triggerid': current['triggerid'],
                      'expression': trigger['expression'],
                      'priority': priority}
            changes.append(Change('update', 'trigger', name,
                                  'trigger.update', params,
                                  current['triggerid']))
        else:
            changes.append(Change('unchanged', 'trigger', name, None, None,
                                  current['triggerid']))
    return changes


def _plan_hosts(zapi, spec, tname, templateid):
    changes = []
    for host in spec.get('hosts') or []:
        name = host['host']
        href = Ref('host', name)
        link_name = '%s -> %s' % (name, tname)
        link = {'hosts': [{'hostid': href}],
                'templates': [{'templateid': Ref('template', tname)}]}
        found = zapi.host.get(filter={'host': name},
                              output=['hostid', 'host'],
                              selectGroups=['groupid', 'name'],
                              selectParentTemplates=['templateid', 'host'])
        if not found:
            params = {
                'host': name,
                'groups': [{'groupid': Ref('hostgroup', g)}
                           for g in host['groups']],
                'interfaces': (list(host.get('interfaces') or []) or
                               [dict(TRAPPER_ONLY_INTERFACE)]),
            }
            changes.append(Change('create', 'host', name, 'host.create',
                                  params, None))
            changes.append(Change('create', 'link', link_name,
                                  'host.massadd', link, None))
            continue
        current = found[0]
        hostid = current['hostid']
        have = set(g['name'] for g in current.get('groups') or [])
        missing = [g for g in host['groups'] if g not in have]
        if missing:
            params = {'hosts': [{'hostid': href}],
                      'groups': [{'groupid': Ref('hostgroup', g)}
                                 for g in missing]}
            changes.append(Change('update', 'host', name, 'host.massadd',
                                  params, hostid))
        else:
            changes.append(Change('unchanged', 'host', name, None, None,
                                  hostid))
        linked = [t['templateid'] for t in
                  current.get('parentTemplates') or []]
        if templateid is not None and templateid in linked:
            changes.append(Change('unchanged', 'link', link_name, None,
                                  None, None))
        else:
            changes.append(Change('create', 'link', link_name,
                                  'host.massadd', link, None))
    return changes


def plan(zapi, spec):
    """Return the ``Change`` list that brings Zabbix in line with
    ``spec``.  Only read (``*.get``) API calls are made."""
    tname = spec['template']
    group_names = [spec['group']]
    for host in spec.get('hosts') or []:
        for group in host.get('groups') or []:
            if group not in group_names:
                group_names.append(group)
    changes = _plan_groups(zapi, group_names)

    found = zapi.template.get(filter={'host': tname},
                              output=['templateid', 'host'])
    templateid = found[0]['templateid'] if found else None
    tref = Ref('template', tname)
    if templateid is None:
        changes.append(Change('create', 'template', tname, 'template.create',
                              {'host': tname,
                               'groups': [{'groupid': Ref('hostgroup',
                                                          spec['group'])}]},
                              None))
        apps, items, triggers = [], [], []
    else:
        changes.append(Change('unchanged', 'template', tname, None, None,
                              templateid))
        apps = zapi.application.get(hostids=[templateid],
                                    output=['applicationid', 'name'])
        items = zapi.item.get(hostids=[templateid],
                              output=['itemid', 'key_', 'name', 'type',
                                      'value_type', 'units', 'delay'],
                              selectApplications=['applicationid', 'name'])
        triggers = zapi.trigger.get(hostids=[templateid],
                                    output=['triggerid', 'description',
                                            'expression', 'priority'],
                                    expandExpression=True)

    app_ids = dict((a['name'], a['applicationid']) for a in apps)
    for app in spec.get('applications') or []:
        if app in app_ids:
            changes.append(Change('unchanged', 'application', app, None,
                                  None, app_ids[app]))
        else:
            changes.append(Change('create', 'application', app,
                                  'application.create',
                                  {'name': app, 'hostid': tref}, None))

    changes.extend(_plan_items(spec, items, tref))
    changes.extend(_plan_triggers(spec, triggers))
    changes.extend(_plan_hosts(zapi, spec, tname, templateid))
    return changes


# --- applying ---------------------------------------------------------------

def _resolve(value, ids):
    if isinstance(value, Ref):
        try:
            return ids[(value.obj, value.name)]
        except KeyError:
            raise SpecError('no id for %s %r' % (value.obj, value.name))
    if isinstance(value, dict):
        return dict((k, _resolve(v, ids)) for k, v in value.items())
    if isinstance(value, list):
        return [_resolve(v, ids) for v in value]
    return value


def apply(zapi, changes):
    """Perform the ``create`` and ``update`` changes in order.  Returns
    ``{(obj, name): id}`` for every object with a known id."""
    ids = {}
    for change in changes:
        if change.id is not None:
            ids[(change.obj, change.name)] = change.id
    for change in changes:
        if change.action == 'unchanged' or change.method is None:
            continue
        api_obj, method = change.method.split('.')
        params = _resolve(change.params, ids)
        result = getattr(getattr(zapi, api_obj), method)(**params)
        if method == 'create':
            created = result[api.id_field(api_obj) + 's'][0]
            ids[(change.obj, change.name)] = created
    return ids


def summary(changes):
    counts = dict((action, 0) for action in ACTIONS)
    for change in changes:
        counts[change.action] += 1
    return counts
