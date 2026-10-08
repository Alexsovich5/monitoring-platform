"""Checks on the image builds and the compose file: packages are fetched
over HTTPS and checked against committed SHA-256 sums, apt is never told
to skip signature checks, published ports only listen on the loopback
address, and the intake secret is shared the way the code expects."""
import os
import re

import yaml

from monplat import intake

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
SKIP_DIRS = ('.git', 'vendor', 'node_modules')

BYPASSES = ('--allow-unauthenticated', '--force-yes', 'trusted=yes',
            'AllowUnauthenticated', '--no-check-gpg',
            '--no-check-certificate', '--insecure', 'allow-insecure',
            'AllowInsecureRepositories', 'curl -k ')


def _files(predicate):
    found = []
    for directory, dirs, names in os.walk(ROOT):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        for name in names:
            path = os.path.join(directory, name)
            if predicate(os.path.relpath(path, ROOT), name):
                found.append(path)
    return sorted(found)


def _dockerfiles():
    return _files(lambda rel, name: name.startswith('Dockerfile'))


def _apt_configs():
    return _files(lambda rel, name: name.endswith('.list') or
                  'apt.conf' in rel or rel.startswith('etc/apt'))


def _read(path):
    with open(path) as handle:
        return handle.read()


def _compose():
    return yaml.safe_load(_read(os.path.join(ROOT, 'docker-compose.yml')))


def test_there_are_dockerfiles_to_check():
    names = [os.path.relpath(p, ROOT) for p in _dockerfiles()]
    assert 'Dockerfile' in names
    assert 'docker/zabbix/Dockerfile' in names
    assert 'docker/grafana/Dockerfile' in names


def test_no_dockerfile_or_apt_config_skips_signature_checks():
    for path in _dockerfiles() + _apt_configs():
        text = _read(path)
        for flag in BYPASSES:
            assert flag not in text, '%s uses %s' % (path, flag)


def test_no_dockerfile_fetches_over_plain_http():
    for path in _dockerfiles():
        assert 'http://' not in _read(path), path


def test_makefile_downloads_only_over_https():
    text = _read(os.path.join(ROOT, 'Makefile'))
    assert 'http://' not in text
    assert 'https://repo.zabbix.com/' in text


def _sums(path):
    sums = {}
    for line in _read(path).splitlines():
        digest, name = line.split()
        assert re.match(r'^[0-9a-f]{64}$', digest), line
        sums[name] = digest
    return sums


def test_vendored_packages_are_checked_against_committed_sums():
    for image, expected in (
            ('zabbix', ('zabbix-server-pgsql_2.4.3-1+trusty_amd64.deb',
                        'zabbix-agent_2.4.3-1+trusty_amd64.deb',
                        'zabbix-frontend-php_2.4.3-1+trusty_all.deb')),
            ('grafana', ('v1.9.1.tar.gz', 'less-1.4.2.tgz',
                         'ycssmin-1.0.1.tgz', 'mkdirp-0.3.5.tgz',
                         'mime-1.2.11.tgz'))):
        directory = os.path.join(ROOT, 'docker', image)
        sums = _sums(os.path.join(directory, 'SHA256SUMS'))
        assert sorted(sums) == sorted(expected)
        dockerfile = _read(os.path.join(directory, 'Dockerfile'))
        assert 'sha256sum -c' in dockerfile, image
        # The check runs before anything from vendor/ is installed.
        check = dockerfile.index('sha256sum -c')
        for use in ('dpkg -i', 'tar xzf', 'npm install'):
            if use in dockerfile:
                assert check < dockerfile.index(use), (image, use)


def test_vendor_directories_are_not_committed():
    ignored = _read(os.path.join(ROOT, '.gitignore')).split()
    assert 'docker/zabbix/vendor/' in ignored
    assert 'docker/grafana/vendor/' in ignored


def test_published_ports_listen_on_loopback_inside_the_lane():
    published = []
    for name, service in _compose()['services'].items():
        for port in service.get('ports') or []:
            published.append((name, port))
            host_ip, host_port, _ = port.split(':')
            assert host_ip == '127.0.0.1', (name, port)
            assert 20700 <= int(host_port) <= 20799, (name, port)
    assert published


def _mounts(service):
    mounts = {}
    for volume in _compose()['services'][service].get('volumes') or []:
        parts = volume.split(':')
        mounts[parts[1]] = (parts[0], parts[2:] == ['ro'])
    return mounts


def test_intake_secret_volume_is_shared_where_needed():
    directory = os.path.dirname(intake.DEFAULT_PATH)
    assert _mounts('app')[directory] == ('monplat-secrets', False)
    assert _mounts('api')[directory] == ('monplat-secrets', True)
    assert _mounts('zabbix')[directory][0] == 'monplat-secrets'
    assert 'monplat-secrets' in _compose()['volumes']
    for service in ('collector', 'forecast', 'snmpsim', 'pushover-stub',
                    'grafana'):
        assert directory not in _mounts(service), service


def test_configured_token_path_is_the_mounted_one():
    cfg = yaml.safe_load(_read(os.path.join(ROOT, 'config', 'monplat.yml')))
    assert intake.token_path(cfg) == intake.DEFAULT_PATH
    script = _read(os.path.join(ROOT, 'docker', 'zabbix', 'alertscripts',
                                'mp_alert.py'))
    assert "TOKEN_FILE = '%s'" % intake.DEFAULT_PATH in script


def test_zabbix_entrypoint_lets_the_zabbix_group_read_the_secret():
    text = _read(os.path.join(ROOT, 'docker', 'zabbix', 'entrypoint.sh'))
    directory = os.path.dirname(intake.DEFAULT_PATH)
    assert 'chgrp zabbix %s' % directory in text
    assert 'chmod 2750 %s' % directory in text


def test_rules_file_override_reaches_api_and_app():
    for service in ('api', 'app'):
        env = _compose()['services'][service].get('environment') or []
        assert 'MONPLAT_REMEDIATION_RULES_FILE' in env, service
