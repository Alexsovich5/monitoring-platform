import os
import textwrap

import pytest

from monplat import config


SAMPLE = textwrap.dedent("""\
    zabbix:
      url: http://zabbix/zabbix
      user: Admin
      server: zabbix
      port: 10051
    forecast:
      window_hours: 6
      items:
        - {host: mp-collector, key: "mp.fs.pused[/]", threshold: 90}
    custom_section:
      anything: [1, 2, 3]
      nested: {a: b}
    """)


@pytest.fixture
def cfg_file(tmpdir):
    path = tmpdir.join('monplat.yml')
    path.write(SAMPLE)
    return str(path)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in list(os.environ):
        if name.startswith('MONPLAT_'):
            monkeypatch.delenv(name)


def test_explicit_path_is_parsed(cfg_file):
    cfg = config.load(cfg_file)
    assert cfg['zabbix']['url'] == 'http://zabbix/zabbix'
    assert cfg['zabbix']['port'] == 10051
    assert cfg['forecast']['items'][0]['key'] == 'mp.fs.pused[/]'


def test_default_path_comes_from_monplat_config(cfg_file, monkeypatch):
    monkeypatch.setenv('MONPLAT_CONFIG', cfg_file)
    cfg = config.load()
    assert cfg['zabbix']['server'] == 'zabbix'


def test_default_path_without_env_is_repo_config(tmpdir, monkeypatch):
    tmpdir.mkdir('config').join('monplat.yml').write('zabbix: {user: x}\n')
    monkeypatch.chdir(tmpdir)
    assert config.load()['zabbix']['user'] == 'x'


def test_env_overrides_zabbix_url(cfg_file, monkeypatch):
    monkeypatch.setenv('MONPLAT_ZABBIX_URL', 'http://other/zabbix')
    cfg = config.load(cfg_file)
    assert cfg['zabbix']['url'] == 'http://other/zabbix'
    assert cfg['zabbix']['user'] == 'Admin'


def test_override_of_key_containing_underscore(cfg_file, monkeypatch):
    monkeypatch.setenv('MONPLAT_FORECAST_WINDOW_HOURS', '12')
    cfg = config.load(cfg_file)
    assert cfg['forecast']['window_hours'] == 12


def test_numeric_override_is_cast_to_int(cfg_file, monkeypatch):
    monkeypatch.setenv('MONPLAT_ZABBIX_PORT', '20751')
    cfg = config.load(cfg_file)
    assert cfg['zabbix']['port'] == 20751
    assert isinstance(cfg['zabbix']['port'], int)


def test_non_numeric_value_for_int_key_raises(cfg_file, monkeypatch):
    monkeypatch.setenv('MONPLAT_ZABBIX_PORT', 'abc')
    with pytest.raises(config.ConfigError):
        config.load(cfg_file)


def test_override_for_unknown_section_is_ignored(cfg_file, monkeypatch):
    monkeypatch.setenv('MONPLAT_NOSUCH_KEY', 'x')
    cfg = config.load(cfg_file)
    assert 'nosuch' not in cfg


def test_missing_file_raises_config_error(tmpdir):
    with pytest.raises(config.ConfigError):
        config.load(str(tmpdir.join('absent.yml')))


def test_missing_default_file_raises_config_error(tmpdir, monkeypatch):
    monkeypatch.setenv('MONPLAT_CONFIG', str(tmpdir.join('absent.yml')))
    with pytest.raises(config.ConfigError):
        config.load()


def test_non_mapping_document_raises_config_error(tmpdir):
    path = tmpdir.join('list.yml')
    path.write('- a\n- b\n')
    with pytest.raises(config.ConfigError):
        config.load(str(path))


def test_unknown_sections_are_kept_as_they_are(cfg_file):
    cfg = config.load(cfg_file)
    assert cfg['custom_section'] == {'anything': [1, 2, 3],
                                     'nested': {'a': 'b'}}


def test_shipped_example_config_loads():
    here = os.path.dirname(os.path.abspath(__file__))
    path = os.path.join(here, '..', '..', 'config', 'monplat.yml')
    cfg = config.load(path)
    assert cfg['zabbix']['port'] == 10051
    assert cfg['database']['monplat_dsn'].startswith('host=db')
    assert cfg['forecast']['items'][0]['target_key'] == \
        'mp.forecast.hours_left[/]'
