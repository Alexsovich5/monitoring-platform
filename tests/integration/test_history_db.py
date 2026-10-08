import time

import pytest

from monplat import db, history

from .conftest import send_until_processed

pytestmark = pytest.mark.integration

HOST = 'mp-test-collect'


def _itemid(zapi, hostid, key):
    items = zapi.item.get(hostids=[hostid], filter={'key_': key},
                          output=['itemid'])
    assert items, 'item %s missing on %s' % (key, HOST)
    return int(items[0]['itemid'])


@pytest.mark.parametrize('key,values', [
    ('mp.load1', [0.25, 1.5, 0.75, 2.125, 3.0]),
    ('mp.net.in.bytes', [10, 2000, 30, 400000, 5]),
])
def test_fetch_returns_sent_values_in_clock_order(cfg, zapi, test_hosts,
                                                  key, values):
    # One day back keeps these points clear of anything sent "now", and
    # one-second spacing keeps a rerun's window clear of earlier runs.
    base = int(time.time()) - 86400
    clocks = [base + i for i in range(len(values))]
    rows = [(HOST, key, value, clock)
            for value, clock in reversed(zip(values, clocks))]
    send_until_processed(cfg, rows, timeout=90)

    itemid = _itemid(zapi, test_hosts[HOST], key)
    conn = db.connect(cfg)
    try:
        deadline = time.time() + 30
        while True:
            points = history.fetch(conn, itemid, clocks[0], clocks[-1])
            conn.rollback()
            if len(points) >= len(values) or time.time() > deadline:
                break
            time.sleep(1)
    finally:
        conn.close()
    assert points == [(float(v), c) for v, c in zip(values, clocks)]
