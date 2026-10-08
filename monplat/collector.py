"""Sample host metrics with psutil and push them to Zabbix trapper items.

``sample()`` reads CPU, load, memory, root filesystem and network counters
and returns them keyed by the item keys of Template MP Linux.  ``run()``
sends one batch per interval with the Zabbix sender protocol, keeping only
keys that the template spec defines as trapper items, so the trapper never
sees a key it would reject.
"""
import os
import sys
import time

import psutil

from monplat import templates
from monplat.zabbix import sender

LINUX_TEMPLATE = 'Template MP Linux'
DEFAULT_HOST = 'mp-collector'
DEFAULT_INTERVAL = 30
CPU_SAMPLE_SECONDS = 1.0


def sample():
    """Return ``{item key: value}`` for the current host."""
    mem = psutil.virtual_memory()
    disk = psutil.disk_usage('/')
    net = psutil.net_io_counters()
    return {
        'mp.cpu.util': psutil.cpu_percent(interval=CPU_SAMPLE_SECONDS),
        'mp.load1': os.getloadavg()[0],
        'mp.mem.pused': mem.percent,
        'mp.fs.pused[/]': disk.percent,
        'mp.net.in.bytes': net.bytes_recv,
        'mp.net.out.bytes': net.bytes_sent,
    }


def template_keys(directory=templates.DEFAULT_DIR, template=LINUX_TEMPLATE):
    """Return the set of trapper item keys of ``template`` in the specs."""
    keys = set()
    for spec in templates.load_specs(directory):
        if spec.get('template') != template:
            continue
        for item in spec.get('items') or []:
            if item.get('type') == 'trapper':
                keys.add(item['key'])
    return keys


def payload(host, values, keys, clock):
    """Build sender rows for the ``values`` whose key is in ``keys``."""
    return [(host, key, values[key], clock)
            for key in sorted(values) if key in keys]


def _send_batch(cfg, host, keys, sample_fn, now):
    zcfg = cfg.get('zabbix') or {}
    values = sample_fn()
    rows = payload(host, values, keys, int(now()))
    return sender.send(zcfg.get('server', 'localhost'),
                       int(zcfg.get('port', 10051)), rows)


def run(cfg, once=False, interval=DEFAULT_INTERVAL, host=None, keys=None,
        sample_fn=None, sleep=None, now=None):
    """Sample and send.  With ``once`` send a single batch and return the
    sender result (``SenderError`` propagates); otherwise send a batch
    every ``interval`` seconds forever, reporting send errors on stderr."""
    host = host or DEFAULT_HOST
    keys = template_keys() if keys is None else keys
    sample_fn = sample_fn or sample
    sleep = sleep or time.sleep
    now = now or time.time
    if once:
        return _send_batch(cfg, host, keys, sample_fn, now)
    while True:
        try:
            result = _send_batch(cfg, host, keys, sample_fn, now)
            sys.stdout.write('%s processed: %d; failed: %d; total: %d\n'
                             % (host, result['processed'], result['failed'],
                                result['total']))
        except sender.SenderError as exc:
            sys.stderr.write('mpctl collect: %s\n' % exc)
        sys.stdout.flush()
        sleep(interval)
