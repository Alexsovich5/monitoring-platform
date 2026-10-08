import collections
import os

import mock
import pytest

from monplat import cli, collector, templates

ROOT = os.path.join(os.path.dirname(__file__), '..', '..')
TEMPLATE_DIR = os.path.join(ROOT, 'config', 'templates')

VMem = collections.namedtuple('VMem', 'total available percent used free')
Disk = collections.namedtuple('Disk', 'total used free percent')
NetIO = collections.namedtuple(
    'NetIO', 'bytes_sent bytes_recv packets_sent packets_recv '
             'errin errout dropin dropout')

CFG = {'zabbix': {'server': 'zbx.example', 'port': 10051}}
OK = {'processed': 6, 'failed': 0, 'total': 6}


@pytest.fixture
def fake_psutil(request):
    patches = [mock.patch.object(collector, 'psutil'),
               mock.patch.object(collector.os, 'getloadavg')]
    ps, loadavg = [p.start() for p in patches]
    for p in patches:
        request.addfinalizer(p.stop)
    ps.cpu_percent.return_value = 12.5
    ps.virtual_memory.return_value = VMem(1000, 400, 60.0, 600, 400)
    ps.disk_usage.return_value = Disk(2000, 1500, 500, 75.0)
    ps.net_io_counters.return_value = NetIO(111, 222, 1, 2, 0, 0, 0, 0)
    loadavg.return_value = (0.75, 0.5, 0.25)
    return ps


def test_sample_maps_psutil_calls_to_template_keys(fake_psutil):
    values = collector.sample()
    assert values == {
        'mp.cpu.util': 12.5,
        'mp.load1': 0.75,
        'mp.mem.pused': 60.0,
        'mp.fs.pused[/]': 75.0,
        'mp.net.in.bytes': 222,
        'mp.net.out.bytes': 111,
    }
    fake_psutil.disk_usage.assert_called_once_with('/')
    fake_psutil.net_io_counters.assert_called_once_with()
    # A blocking interval is needed: without one psutil 2.1 compares with
    # the previous call and the first sample is meaningless.
    args, kwargs = fake_psutil.cpu_percent.call_args
    assert (args or (kwargs.get('interval'),))[0] > 0


def test_sampled_keys_are_trapper_items_of_shipped_template(fake_psutil):
    keys = collector.template_keys(TEMPLATE_DIR)
    assert set(collector.sample()) <= keys
    assert 'mp.forecast.hours_left[/]' in keys


def test_template_keys_only_lists_trapper_items():
    spec = templates.Spec({'template': 'Template MP Linux', 'items': [
        {'key': 'a', 'type': 'trapper'}, {'key': 'b', 'type': 'agent'}]})
    with mock.patch.object(collector.templates, 'load_specs',
                           return_value=[spec]):
        assert collector.template_keys('ignored') == set(['a'])


def test_payload_contains_only_template_keys():
    rows = collector.payload('h1', {'mp.cpu.util': 1.0, 'mp.bogus': 2,
                                    'mp.load1': 0.5},
                             set(['mp.cpu.util', 'mp.load1', 'mp.other']),
                             clock=1400000000)
    assert rows == [('h1', 'mp.cpu.util', 1.0, 1400000000),
                    ('h1', 'mp.load1', 0.5, 1400000000)]


def test_run_once_sends_one_batch_filtered_to_template_keys():
    values = {'mp.cpu.util': 3.0, 'mp.not.in.template': 1}
    with mock.patch.object(collector.sender, 'send',
                           return_value=OK) as send:
        result = collector.run(CFG, once=True, host='h1',
                               keys=set(['mp.cpu.util']),
                               sample_fn=lambda: values, now=lambda: 1500)
    assert result == OK
    send.assert_called_once_with('zbx.example', 10051,
                                 [('h1', 'mp.cpu.util', 3.0, 1500)])


def test_run_loops_on_interval_until_stopped():
    sleep = mock.Mock(side_effect=[None, None, KeyboardInterrupt])
    with mock.patch.object(collector.sender, 'send',
                           return_value=OK) as send:
        with pytest.raises(KeyboardInterrupt):
            collector.run(CFG, once=False, interval=30, host='h1',
                          keys=set(['k']), sample_fn=lambda: {'k': 1},
                          sleep=sleep)
    assert send.call_count == 3
    assert sleep.call_args_list == [mock.call(30)] * 3


def test_run_loop_survives_sender_errors():
    sleep = mock.Mock(side_effect=[None, KeyboardInterrupt])
    send = mock.Mock(side_effect=[collector.sender.SenderError('down'), OK])
    with mock.patch.object(collector.sender, 'send', send):
        with pytest.raises(KeyboardInterrupt):
            collector.run(CFG, once=False, interval=5, host='h1',
                          keys=set(['k']), sample_fn=lambda: {'k': 1},
                          sleep=sleep)
    assert send.call_count == 2


def test_run_defaults_to_collector_host():
    with mock.patch.object(collector.sender, 'send',
                           return_value=OK) as send:
        collector.run(CFG, once=True, keys=set(['k']),
                      sample_fn=lambda: {'k': 1}, now=lambda: 7)
    assert send.call_args[0][2] == [('mp-collector', 'k', 1, 7)]


def test_cli_collect_once_sends_exactly_one_batch(fake_psutil, capsys):
    with mock.patch('monplat.config.load', return_value=CFG), \
            mock.patch.object(collector.sender, 'send',
                              return_value=OK) as send, \
            mock.patch.object(collector.time, 'sleep') as sleep:
        code = cli.main(['collect', '--once', '--host', 'mp-test-collect'])
    assert code == 0
    assert send.call_count == 1
    sleep.assert_not_called()
    rows = send.call_args[0][2]
    assert set(r[0] for r in rows) == set(['mp-test-collect'])
    assert len(rows) == 6
    out, _ = capsys.readouterr()
    assert 'processed: 6; failed: 0; total: 6' in out


def test_cli_collect_once_returns_one_when_trapper_unreachable(fake_psutil):
    with mock.patch('monplat.config.load', return_value=CFG), \
            mock.patch.object(collector.sender, 'send',
                              side_effect=collector.sender.SenderError('x')):
        assert cli.main(['collect', '--once']) == 1


def test_cli_collect_rejects_once_with_interval():
    with pytest.raises(SystemExit) as exc:
        cli.main(['collect', '--once', '--interval', '5'])
    assert exc.value.code == 2


def test_cli_collect_passes_interval(fake_psutil):
    with mock.patch('monplat.config.load', return_value=CFG), \
            mock.patch.object(collector, 'run', return_value=OK) as run:
        cli.main(['collect', '--interval', '12'])
    kwargs = run.call_args[1]
    assert kwargs['once'] is False
    assert kwargs['interval'] == 12
