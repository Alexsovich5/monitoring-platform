import os
import subprocess
import tempfile
import time

import pytest
import yaml

from monplat import config, db

from .conftest import send_until_processed, wait_for

pytestmark = pytest.mark.integration

HOST = 'mp-test-forecast'
SOURCE_KEY = 'mp.fs.pused[/]'
TARGET_KEY = 'mp.forecast.hours_left[/]'
TRIGGER = 'Root FS full within horizon on %s' % HOST


def _history_rows(cfg, host, key, since):
    conn = db.connect(cfg)
    try:
        cursor = conn.cursor()
        cursor.execute('SELECT count(*) FROM history h '
                       'JOIN items i ON i.itemid = h.itemid '
                       'JOIN hosts o ON o.hostid = i.hostid '
                       'WHERE o.host = %s AND i.key_ = %s AND h.clock >= %s',
                       (host, key, since))
        return cursor.fetchone()[0]
    finally:
        conn.close()


def _forecast_values(zapi, hostid, since):
    items = zapi.item.get(hostids=[hostid], filter={'key_': TARGET_KEY},
                          output=['itemid'])
    assert items, '%s missing on %s' % (TARGET_KEY, HOST)
    return zapi.history.get(itemids=[items[0]['itemid']], history=0,
                            time_from=since, output='extend')


def _trigger_in_problem(zapi, hostid):
    return zapi.trigger.get(hostids=[hostid], filter={'value': 1},
                            search={'description': 'full within horizon'},
                            output=['triggerid', 'description', 'value'])


def test_forecast_pushes_hours_left_and_fires_trigger(cfg, zapi, test_hosts):
    hostid = test_hosts[HOST]
    now = int(time.time())
    # 70 % .. 81 %, one point per hour, the last one now: 9 h until 90 %.
    rows = [(HOST, SOURCE_KEY, 70.0 + i, now - (11 - i) * 3600)
            for i in range(12)]
    send_until_processed(cfg, rows, timeout=90)
    wait_for(lambda: _history_rows(cfg, HOST, SOURCE_KEY,
                                   now - 6 * 3600) >= 7,
             timeout=60, message='synthetic %s history' % SOURCE_KEY)

    settings = config.load()
    settings['forecast'] = {
        'window_hours': 6, 'horizon_hours': 24,
        'items': [{'host': HOST, 'key': SOURCE_KEY, 'threshold': 90,
                   'target_key': TARGET_KEY}],
    }
    handle, path = tempfile.mkstemp(suffix='.yml')
    try:
        with os.fdopen(handle, 'w') as out:
            yaml.safe_dump(settings, out, default_flow_style=False)
        env = dict(os.environ, MONPLAT_CONFIG=path)
        proc = subprocess.Popen(['mpctl', 'forecast', '--once'], env=env,
                                stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE)
        out, err = proc.communicate()
    finally:
        os.remove(path)
    assert proc.returncode == 0, out + err
    assert 'processed: 1; failed: 0; total: 1' in out, out + err

    values = wait_for(lambda: _forecast_values(zapi, hostid, now - 60),
                      timeout=60, message='%s history' % TARGET_KEY)
    assert abs(float(values[-1]['value']) - 9.0) <= 0.5, values

    triggers = wait_for(lambda: _trigger_in_problem(zapi, hostid),
                        timeout=90, message='%s in PROBLEM' % TRIGGER)
    assert triggers[0]['description'].startswith('Root FS full within')
