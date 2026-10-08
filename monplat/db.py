"""psycopg2 connections built from the ``database`` config section."""
import psycopg2


def connect(cfg, name='zabbix'):
    """Open a connection using ``cfg['database']['<name>_dsn']``; ``name``
    is ``zabbix`` (Zabbix history, read-only) or ``monplat``."""
    return psycopg2.connect(cfg['database']['%s_dsn' % name])
