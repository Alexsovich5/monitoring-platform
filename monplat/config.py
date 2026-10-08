"""Load the monplat YAML configuration with environment overrides.

The file path is taken from the ``path`` argument, then ``MONPLAT_CONFIG``,
then ``config/monplat.yml``.  Any scalar can be overridden with an
environment variable ``MONPLAT_<SECTION>_<KEY>`` (upper case), e.g.
``MONPLAT_ZABBIX_URL`` or ``MONPLAT_FORECAST_WINDOW_HOURS``.  Override
values are cast to the type of the value already in the file (int, float,
bool); values for keys absent from the file are cast to int when they are
all digits and kept as strings otherwise.
"""
import os

import yaml

DEFAULT_PATH = os.path.join('config', 'monplat.yml')
ENV_PREFIX = 'MONPLAT_'
PATH_VAR = 'MONPLAT_CONFIG'

_TRUE = ('1', 'true', 'yes', 'on')
_FALSE = ('0', 'false', 'no', 'off')


class ConfigError(Exception):
    pass


def _cast(name, raw, current):
    try:
        if isinstance(current, bool):
            lowered = raw.strip().lower()
            if lowered in _TRUE:
                return True
            if lowered in _FALSE:
                return False
            raise ValueError(raw)
        if isinstance(current, (int, long)):
            return int(raw)
        if isinstance(current, float):
            return float(raw)
    except ValueError:
        raise ConfigError('%s=%r is not a valid %s'
                          % (name, raw, type(current).__name__))
    if current is None and raw.strip().isdigit():
        return int(raw)
    return raw


def _apply_overrides(cfg, environ):
    # Longest section names first, so "a_b" wins over "a" for MONPLAT_A_B_C.
    sections = sorted((s for s in cfg if isinstance(cfg[s], dict)),
                      key=len, reverse=True)
    for name in sorted(environ):
        if not name.startswith(ENV_PREFIX) or name == PATH_VAR:
            continue
        rest = name[len(ENV_PREFIX):].lower()
        for section in sections:
            prefix = section.lower() + '_'
            if rest.startswith(prefix) and len(rest) > len(prefix):
                key = rest[len(prefix):]
                values = cfg[section]
                values[key] = _cast(name, environ[name], values.get(key))
                break
    return cfg


def load(path=None):
    """Return the configuration as a dict of sections."""
    environ = os.environ
    if path is None:
        path = environ.get(PATH_VAR) or DEFAULT_PATH
    try:
        with open(path) as handle:
            cfg = yaml.safe_load(handle)
    except IOError as exc:
        raise ConfigError('cannot read config %s: %s' % (path, exc.strerror))
    except yaml.YAMLError as exc:
        raise ConfigError('invalid YAML in %s: %s' % (path, exc))
    if cfg is None:
        cfg = {}
    if not isinstance(cfg, dict):
        raise ConfigError('%s must contain a mapping of sections' % path)
    return _apply_overrides(cfg, environ)
