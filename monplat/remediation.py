"""Run Zabbix global scripts for PROBLEM events that match a rule.

Rules live in ``config/remediation.yml``::

    rules:
      - name: clear-spool
        trigger_match: "^Spool directory too large"   # re.match on the name
        min_severity: warning                         # name or 0-5
        script: "MP clear spool"                      # Zabbix global script
        command: "rm -f /var/spool/mp-demo/* && echo cleared"
        expect_output: cleared                        # optional
        cooldown_seconds: 600

``mpctl provision`` creates each rule's global script from ``script`` and
``command`` (see ``monplat.actions.plan_scripts``).  ``remediate()`` runs
the script on the event's host through ``script.execute``, which has the
Zabbix server ask the host's agent to run the command.  A run counts as
successful only when Zabbix reports success and, if the rule sets
``expect_output``, the output contains that text.  Every run, successful
or not, is written to ``monplat.remediations``; a rule does not run again
for the same host until ``cooldown_seconds`` have passed since its last
run.
"""
import collections
import datetime
import logging
import os
import re

import yaml

from monplat.events import UTC
from monplat.templates import SEVERITIES, SpecError

DEFAULT_PATH = os.path.join('config', 'remediation.yml')

Rule = collections.namedtuple(
    'Rule', 'name pattern min_severity script command expect_output '
            'cooldown_seconds')

_REQUIRED = ('name', 'trigger_match', 'min_severity', 'script', 'command',
             'cooldown_seconds')

_RECENT_RUN = ('SELECT 1 FROM remediations '
               'WHERE host = %s AND rule = %s AND executed_at > %s LIMIT 1')
_INSERT = ('INSERT INTO remediations '
           '(event_id, rule, host, script_name, ok, output) '
           'VALUES (%s, %s, %s, %s, %s, %s)')

log = logging.getLogger(__name__)


def _severity(value, where):
    if isinstance(value, int) and 0 <= value <= 5:
        return value
    if value in SEVERITIES:
        return SEVERITIES[value]
    raise SpecError('%s: unknown min_severity %r' % (where, value))


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
    return Rule(name=str(data['name']),
                pattern=pattern,
                min_severity=_severity(data['min_severity'], where),
                script=str(data['script']),
                command=str(data['command']),
                expect_output=data.get('expect_output') or None,
                cooldown_seconds=cooldown)


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


def match(rules, event):
    """Return the first rule for a PROBLEM ``event`` whose pattern matches
    the trigger name at its start and whose minimum severity is reached,
    or None.  OK events never match."""
    if event.status != 'PROBLEM':
        return None
    for rule in rules:
        if (event.severity >= rule.min_severity and
                rule.pattern.match(event.trigger_name)):
            return rule
    return None


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


def record(conn, event_id, rule, host, ok, output):
    """Append one run to ``monplat.remediations``."""
    cursor = conn.cursor()
    try:
        cursor.execute(_INSERT, (event_id, rule.name, host, rule.script,
                                 ok, output))
        conn.commit()
    finally:
        cursor.close()


def result(rule, host, ran, ok, output):
    return {'rule': rule.name, 'script': rule.script, 'host': host,
            'ran': ran, 'ok': ok, 'output': output}


def _hostid(zapi, host):
    # {HOST.NAME} is the visible name, which defaults to the host name.
    for field in ('host', 'name'):
        found = zapi.host.get(filter={field: host}, output=['hostid', 'host'])
        if found:
            return found[0]['hostid']
    return None


def _execute(zapi, rule, host):
    """Run the rule's script on ``host``; return ``(ok, output)``."""
    scripts = zapi.script.get(filter={'name': rule.script},
                              output=['scriptid', 'name'])
    if not scripts:
        return False, 'global script %r does not exist' % rule.script
    hostid = _hostid(zapi, host)
    if hostid is None:
        return False, 'host %r does not exist in Zabbix' % host
    reply = zapi.script.execute(scriptid=scripts[0]['scriptid'],
                                hostid=hostid)
    output = reply.get('value') or ''
    if reply.get('response') != 'success':
        return False, output or 'script.execute reported failure'
    if rule.expect_output and rule.expect_output not in output:
        return False, output
    return True, output


def remediate(zapi, conn, event, rules, event_id, now=None):
    """Run the matching rule's script for ``event`` (stored as row
    ``event_id`` in ``events``).  Returns None when no rule matches, or a
    dict ``{rule, script, host, ran, ok, output}``."""
    rule = match(rules, event)
    if rule is None:
        return None
    now = now or datetime.datetime.now(UTC)
    if in_cooldown(conn, event.host, rule, now):
        return result(rule, event.host, False, None,
                      'skipped: %s ran for %s within the last %d s cooldown'
                      % (rule.name, event.host, rule.cooldown_seconds))
    try:
        ok, output = _execute(zapi, rule, event.host)
    except Exception as exc:  # API errors, timeouts, refused connections
        ok, output = False, '%s: %s' % (type(exc).__name__, exc)
    record(conn, event_id, rule, event.host, ok, output)
    if ok:
        log.info('remediation %s on %s succeeded', rule.name, event.host)
    else:
        log.warning('remediation %s on %s failed: %s', rule.name,
                    event.host, output)
    return result(rule, event.host, True, ok, output)
