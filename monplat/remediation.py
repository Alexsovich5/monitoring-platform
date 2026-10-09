"""Run Zabbix global scripts for PROBLEM events that match a rule.

Rules live in ``config/remediation.yml`` (``remediation.rules_file`` in
the config)::

    rules:
      - name: clear-spool
        enabled: false                                # opt-in per rule
        trigger_match: "^Spool directory too large"   # re.match on the name
        min_severity: warning                         # name or 0-5
        script: "MP clear spool"                      # Zabbix global script
        command: "rm -f /var/spool/mp-demo/* && echo cleared"
        expect_output: cleared                        # optional
        cooldown_seconds: 600
        allow_hosts: ["Zabbix server"]                # and/or allow_groups

``mpctl provision`` creates the global script of each enabled rule from
``script`` and ``command`` (see ``monplat.actions.plan_scripts``).

The notification that reaches the API is not trusted for anything but the
event id.  ``remediate()`` fetches the event and its trigger from the
Zabbix API and only goes on when the event exists, is a trigger PROBLEM
that is neither acknowledged nor already resolved (its trigger is still in
PROBLEM and last changed at the event), belongs to the trigger id and the
single host named in the message, and the trigger's own name and severity
match the rule.  The host must then be in the rule's ``allow_hosts`` or in
one of its ``allow_groups``.  The script runs through ``script.execute``
on the hostid reported by Zabbix.

Every outcome is written to ``monplat.remediations``: runs (``ran``
true, ``ok`` telling whether Zabbix reported success and, if the rule sets
``expect_output``, the output contains it) and refusals, including rate
limiting (``ran`` false).  A rule does not run again for the same host
until ``cooldown_seconds`` have passed since its last run.

Concurrent deliveries cannot run a script twice.  Before ``script.execute``
a "claimed" row (``ran`` true) is committed in the same transaction as the
cooldown check, behind ``pg_advisory_xact_lock`` on the host and rule; the
row is updated with the outcome afterwards.  A unique index on
``(event_id, rule)`` among ``ran`` rows also stops one event from running a
rule twice when the cooldown is 0.
"""
import collections
import datetime
import logging
import os
import re

import psycopg2
import yaml

from monplat.events import UTC
from monplat.templates import SEVERITIES, SpecError

DEFAULT_PATH = os.path.join('config', 'remediation.yml')

Rule = collections.namedtuple(
    'Rule', 'name enabled pattern min_severity script command expect_output '
            'cooldown_seconds allow_hosts allow_groups')

# Zabbix 2.4 event and trigger values.
SOURCE_TRIGGERS = '0'
OBJECT_TRIGGER = '0'
VALUE_PROBLEM = '1'

_REQUIRED = ('name', 'trigger_match', 'min_severity', 'script', 'command',
             'cooldown_seconds')

_RECENT_RUN = ('SELECT 1 FROM remediations '
               'WHERE host = %s AND rule = %s AND executed_at > %s AND ran '
               'LIMIT 1')
_LOCK = 'SELECT pg_advisory_xact_lock(hashtext(%s))'
_FINISH = ('UPDATE remediations SET ok = %s, output = %s, '
           'executed_at = now() '
           'WHERE event_id = %s AND rule = %s AND ran')
CLAIMED = 'claimed: script not finished'
_INSERT = ('INSERT INTO remediations '
           '(event_id, rule, host, script_name, ok, output, ran) '
           'VALUES (%s, %s, %s, %s, %s, %s, %s)')


class Refused(Exception):
    """The event is not acted on; the message says why."""

log = logging.getLogger(__name__)


def _severity(value, where):
    if isinstance(value, int) and 0 <= value <= 5:
        return value
    if value in SEVERITIES:
        return SEVERITIES[value]
    raise SpecError('%s: unknown min_severity %r' % (where, value))


def rules_path(cfg):
    """Return the rules file named by the config, or ``DEFAULT_PATH``."""
    return (cfg.get('remediation') or {}).get('rules_file') or DEFAULT_PATH


def _names(data, field, where):
    value = data.get(field)
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
            isinstance(v, basestring) and v for v in value):
        raise SpecError('%s: %s must be a list of names' % (where, field))
    return tuple(value)


def _rule(data, where):
    if not isinstance(data, dict):
        raise SpecError('%s: a rule must be a mapping' % where)
    for field in _REQUIRED:
        if data.get(field) in (None, ''):
            raise SpecError('%s: missing %r' % (where, field))
    where = '%s (%s)' % (where, data['name'])
    try:
        pattern = re.compile(data['trigger_match'])
    except re.error as exc:
        raise SpecError('%s: trigger_match %r: %s'
                        % (where, data['trigger_match'], exc))
    try:
        cooldown = int(data['cooldown_seconds'])
    except (TypeError, ValueError):
        cooldown = -1
    if cooldown < 0:
        raise SpecError('%s: cooldown_seconds must be a number >= 0' % where)
    enabled = data.get('enabled', False)
    if not isinstance(enabled, bool):
        raise SpecError('%s: enabled must be true or false' % where)
    allow_hosts = _names(data, 'allow_hosts', where)
    allow_groups = _names(data, 'allow_groups', where)
    if not allow_hosts and not allow_groups:
        raise SpecError('%s: needs allow_hosts and/or allow_groups, the '
                        'hosts the script may run on' % where)
    return Rule(name=str(data['name']),
                enabled=enabled,
                pattern=pattern,
                min_severity=_severity(data['min_severity'], where),
                script=str(data['script']),
                command=str(data['command']),
                expect_output=data.get('expect_output') or None,
                cooldown_seconds=cooldown,
                allow_hosts=allow_hosts,
                allow_groups=allow_groups)


def load_rules(path=DEFAULT_PATH):
    """Read and check the rules file; raises ``SpecError``."""
    try:
        with open(path) as handle:
            data = yaml.safe_load(handle)
    except (IOError, yaml.YAMLError) as exc:
        raise SpecError('%s: %s' % (path, exc))
    raw = data.get('rules') if isinstance(data, dict) else None
    if not raw or not isinstance(raw, list):
        raise SpecError('%s: no rules defined' % path)
    rules = [_rule(r, '%s: rule %d' % (path, i + 1))
             for i, r in enumerate(raw)]
    names = [r.name for r in rules]
    for name in names:
        if names.count(name) > 1:
            raise SpecError('%s: duplicate rule name %r' % (path, name))
    return rules


def _rule_for(rules, trigger_name, severity):
    for rule in rules:
        if (rule.enabled and severity >= rule.min_severity and
                rule.pattern.match(trigger_name)):
            return rule
    return None


def match(rules, event):
    """Return the first enabled rule for a PROBLEM ``event`` whose pattern
    matches the trigger name at its start and whose minimum severity is
    reached, or None.  OK events never match."""
    if event.status != 'PROBLEM':
        return None
    return _rule_for(rules, event.trigger_name, event.severity)


def in_cooldown(conn, host, rule, now):
    """True when ``rule`` already ran for ``host`` less than
    ``rule.cooldown_seconds`` before ``now``."""
    since = now - datetime.timedelta(seconds=rule.cooldown_seconds)
    cursor = conn.cursor()
    try:
        cursor.execute(_RECENT_RUN, (host, rule.name, since))
        return cursor.fetchone() is not None
    finally:
        cursor.close()


def claim(conn, event_id, rule, host, now):
    """Reserve the run of ``rule`` on ``host`` for event row ``event_id``
    and commit the reservation, or raise ``Refused``.  The cooldown check
    and the insert share one transaction behind an advisory lock on
    ``host`` and ``rule``, so two deliveries cannot both pass."""
    cursor = conn.cursor()
    try:
        cursor.execute(_LOCK, ('%s/%s' % (host, rule.name),))
        if in_cooldown(conn, host, rule, now):
            conn.rollback()
            raise Refused('rate-limited: %s ran for %s within the last '
                          '%d s' % (rule.name, host, rule.cooldown_seconds))
        try:
            cursor.execute(_INSERT, (event_id, rule.name, host, rule.script,
                                     False, CLAIMED, True))
        except psycopg2.IntegrityError:
            conn.rollback()
            raise Refused('%s already ran for this event' % rule.name)
        conn.commit()
    except Refused:
        raise
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()


def finish(conn, event_id, rule, ok, output):
    """Store the outcome of the run claimed with ``claim``."""
    cursor = conn.cursor()
    try:
        cursor.execute(_FINISH, (ok, output, event_id, rule.name))
        conn.commit()
    finally:
        cursor.close()


def record(conn, event_id, rule, host, ok, output, ran=True):
    """Append one outcome to ``monplat.remediations``; ``ran`` is false
    for refusals."""
    cursor = conn.cursor()
    try:
        cursor.execute(_INSERT, (event_id, rule.name, host, rule.script,
                                 ok, output, ran))
        conn.commit()
    finally:
        cursor.close()


def result(rule, host, ran, ok, output):
    return {'rule': rule.name, 'script': rule.script, 'host': host,
            'ran': ran, 'ok': ok, 'output': output}


def _confirm(zapi, event, rule):
    """Return ``(hostid, host)`` from Zabbix for the PROBLEM behind
    ``event``, or raise ``Refused``."""
    found = zapi.event.get(eventids=[event.eventid], output='extend',
                           selectHosts=['hostid', 'host'])
    if not found:
        raise Refused('event %d not found in Zabbix' % event.eventid)
    zevent = found[0]
    if (str(zevent.get('source')) != SOURCE_TRIGGERS or
            str(zevent.get('object')) != OBJECT_TRIGGER):
        raise Refused('event %d is not a trigger event' % event.eventid)
    if str(zevent.get('value')) != VALUE_PROBLEM:
        raise Refused('event %d is not a PROBLEM in Zabbix' % event.eventid)
    if str(zevent.get('acknowledged', '0')) != '0':
        raise Refused('event %d is acknowledged' % event.eventid)
    if str(zevent.get('objectid')) != str(event.trigger_id):
        raise Refused('event %d belongs to trigger %s, not %d'
                      % (event.eventid, zevent.get('objectid'),
                         event.trigger_id))
    hosts = zevent.get('hosts') or []
    if len(hosts) != 1:
        raise Refused('event %d does not belong to exactly one host'
                      % event.eventid)
    hostid, host = hosts[0]['hostid'], hosts[0]['host']
    if host != event.host:
        raise Refused('message names host %r but Zabbix has %r'
                      % (event.host, host))
    triggers = zapi.trigger.get(triggerids=[zevent['objectid']],
                                output=['triggerid', 'description',
                                        'priority', 'value', 'lastchange'],
                                expandDescription=True)
    if not triggers:
        raise Refused('trigger %s not found in Zabbix' % zevent['objectid'])
    trigger = triggers[0]
    if str(trigger.get('value')) != VALUE_PROBLEM:
        raise Refused('trigger %s is no longer in PROBLEM'
                      % trigger['triggerid'])
    if int(zevent.get('clock', 0)) < int(trigger.get('lastchange', 0)):
        raise Refused('event %d is not the current problem of trigger %s'
                      % (event.eventid, trigger['triggerid']))
    if _rule_for([rule], trigger.get('description', ''),
                 int(trigger.get('priority', -1))) is None:
        raise Refused('trigger %r (severity %s) in Zabbix does not match '
                      'rule %s' % (trigger.get('description'),
                                   trigger.get('priority'), rule.name))
    return hostid, host


def _check_allowed(zapi, rule, hostid, host):
    if host in rule.allow_hosts:
        return
    if rule.allow_groups:
        found = zapi.host.get(hostids=[hostid], output=['hostid', 'host'],
                              selectGroups=['name'])
        groups = set(g['name'] for h in found for g in h.get('groups') or [])
        if groups.intersection(rule.allow_groups):
            return
    raise Refused('host %r is not in the allow list of rule %s'
                  % (host, rule.name))


def _execute(zapi, rule, hostid):
    """Run the rule's script on ``hostid``; return ``(ok, output)``."""
    scripts = zapi.script.get(filter={'name': rule.script},
                              output=['scriptid', 'name'])
    if len(scripts) != 1:
        return False, ('expected one global script named %r, found %d'
                       % (rule.script, len(scripts)))
    reply = zapi.script.execute(scriptid=scripts[0]['scriptid'],
                                hostid=hostid)
    output = reply.get('value') or ''
    if reply.get('response') != 'success':
        return False, output or 'script.execute reported failure'
    if rule.expect_output and rule.expect_output not in output:
        return False, output
    return True, output


def _refuse(conn, event_id, rule, host, reason):
    output = 'refused: %s' % reason
    record(conn, event_id, rule, host, False, output, ran=False)
    log.warning('remediation %s for %s %s', rule.name, host, output)
    return result(rule, host, False, False, output)


def remediate(zapi, conn, event, rules, event_id, now=None):
    """Run the matching rule's script for ``event`` (stored as row
    ``event_id`` in ``events``) once Zabbix confirms it.  Returns None when
    no enabled rule matches, or a dict ``{rule, script, host, ran, ok,
    output}``."""
    rule = match(rules, event)
    if rule is None:
        return None
    now = now or datetime.datetime.now(UTC)
    try:
        hostid, host = _confirm(zapi, event, rule)
        _check_allowed(zapi, rule, hostid, host)
    except Refused as exc:
        return _refuse(conn, event_id, rule, event.host, str(exc))
    except Exception as exc:  # API errors, timeouts, refused connections
        return _refuse(conn, event_id, rule, event.host,
                       'cannot confirm the event with Zabbix: %s: %s'
                       % (type(exc).__name__, exc))
    try:
        claim(conn, event_id, rule, host, now)
    except Refused as exc:
        return _refuse(conn, event_id, rule, host, str(exc))
    try:
        ok, output = _execute(zapi, rule, hostid)
    except Exception as exc:  # API errors, timeouts, refused connections
        ok, output = False, '%s: %s' % (type(exc).__name__, exc)
    finish(conn, event_id, rule, ok, output)
    if ok:
        log.info('remediation %s on %s succeeded', rule.name, host)
    else:
        log.warning('remediation %s on %s failed: %s', rule.name, host,
                    output)
    return result(rule, host, True, ok, output)
