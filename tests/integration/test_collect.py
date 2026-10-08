import re
import subprocess
import time

import pytest

from monplat import collector

from .conftest import send_until_processed, wait_for

pytestmark = pytest.mark.integration

HOST = 'mp-test-collect'
_RESULT = re.compile(r'processed: (\d+); failed: (\d+); total: (\d+)')


def _history(zapi, hostid, key, since):
    items = zapi.item.get(hostids=[hostid], filter={'key_': key},
                          output=['itemid', 'value_type'])
    assert items, 'item %s missing on %s' % (key, HOST)
    return zapi.history.get(itemids=[items[0]['itemid']],
                            history=int(items[0]['value_type']),
                            time_from=since, output='extend')


def test_collect_once_pushes_host_metrics(cfg, zapi, test_hosts):
    keys = sorted(collector.sample())
    # Warm-up values carry an old clock so they cannot satisfy the history
    # check below; they only prove the host's items are in the cache.
    old = int(time.time()) - 3600
    send_until_processed(cfg, [(HOST, key, 0, old) for key in keys],
                         timeout=90)

    since = int(time.time())
    proc = subprocess.Popen(['mpctl', 'collect', '--once', '--host', HOST],
                            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    out, err = proc.communicate()
    assert proc.returncode == 0, out + err
    match = _RESULT.search(out)
    assert match, out
    processed, failed, total = [int(n) for n in match.groups()]
    assert (processed, failed, total) == (len(keys), 0, len(keys))

    rows = wait_for(lambda: _history(zapi, test_hosts[HOST], 'mp.cpu.util',
                                     since),
                    timeout=60, message='mp.cpu.util history for %s' % HOST)
    assert 0.0 <= float(rows[-1]['value']) <= 100.0
