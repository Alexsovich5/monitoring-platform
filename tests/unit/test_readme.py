import io
import os
import re
import subprocess
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
README = os.path.join(ROOT, 'README.md')

# Backticked tokens that name a repository file: a relative path with a
# directory part, or a bare file name with one of these extensions.
FILE_EXTENSIONS = ('.py', '.yml', '.yaml', '.sh', '.sql', '.json', '.md',
                   '.txt', '.ini', '.cfg', '.conf', '.php', '.snmprec', '.js')
BARE_FILES = ('Makefile', 'Dockerfile')


def _git(*args):
    proc = subprocess.Popen(('git',) + args, cwd=ROOT, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE)
    out, err = proc.communicate()
    assert proc.returncode == 0, err
    return out.decode('utf-8')


def _readme():
    with io.open(README, encoding='utf-8') as f:
        return f.read()


def _section(text, heading):
    match = re.search(r'^## %s\n(.*?)(?=^## |\Z)' % re.escape(heading), text,
                      re.M | re.S)
    assert match, 'README has no "## %s" section' % heading
    return match.group(1)


def _root_readme_values():
    root = _git('rev-list', '--max-parents=0', 'HEAD').split()[0]
    original = _git('show', '%s:README.md' % root)
    values = []
    for label in ('Timeline', 'Role'):
        match = re.search(r'^\W*%s\W*:\s*(.+?)\s*$' % label, original, re.M)
        assert match, 'root README has no %s line' % label
        value = match.group(1)
        values.append(value)
        values.extend(p.strip() for p in value.split(' - ') if p.strip())
    return values


def test_layout_block_matches_tree_of_git_ls_files():
    files = _git('ls-files')
    proc = subprocess.Popen([sys.executable,
                             os.path.join(ROOT, 'scripts', 'tree.py')],
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE)
    expected, _ = proc.communicate(files.encode('utf-8'))
    assert proc.returncode == 0
    layout = _section(_readme(), 'Layout')
    block = re.search(r'```\n(.*?)```', layout, re.S)
    assert block, 'Layout section has no code block'
    assert block.group(1) == expected.decode('utf-8')


def test_every_path_named_in_readme_exists():
    named = set()
    for token in re.findall(r'`([^`\n]+)`', _readme()):
        if not re.match(r'^[A-Za-z0-9_.\-/]+$', token) or token.startswith('/'):
            continue
        if '/' in token or token.endswith(FILE_EXTENSIONS) \
                or token in BARE_FILES:
            named.add(token.rstrip('/'))
    assert named, 'expected the README to name repository files'
    missing = sorted(p for p in named
                     if not os.path.exists(os.path.join(ROOT, p)))
    assert missing == []


def test_no_template_placeholders_remain():
    text = _readme()
    assert '{{' not in text
    assert '}}' not in text
    assert 'Rules for this README' not in text


def test_no_uptime_or_percentage_business_claims():
    text = _readme()
    assert not re.search(r'\d\s*%', text)
    assert not re.search(r'\bpercent\b', text, re.I)
    for claim in (r'\buptime\b', r'\bMTTR\b', r'\bSLA\b', r'cost sav',
                  r'\breduced\b', r'\bdowntime\b'):
        assert not re.search(claim, text, re.I), claim


def test_no_timeline_or_role_lines():
    assert not re.search(r'^\W*(Timeline|Role)\W*:', _readme(), re.M | re.I)


def test_no_employer_role_or_timeline_values_from_root_readme():
    text = _readme().lower()
    values = _root_readme_values()
    assert len(values) >= 2
    for value in values:
        assert value.lower() not in text


def test_readme_states_period_stack_and_simulated_integrations():
    text = _readme()
    assert ('Personal project built on the 2014-era stack (Zabbix 2.4.3, '
            'Python 2.7, PostgreSQL 9.3, Grafana 1.9.1)') in text
    assert ('SNMP devices are simulated with snmpsim; Pushover is a local '
            'stub; no real push is sent. The monitored servers are the '
            "stack's own containers; no real servers are monitored.") in text
