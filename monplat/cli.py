"""Command line entry point for the monplat toolkit (``mpctl``)."""
import argparse
import sys

from monplat import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog='mpctl',
        description='Provision, collect and query the monitoring platform.')
    parser.add_argument('--version', action='version', version=__version__)
    commands = parser.add_subparsers(dest='command', title='commands')

    send = commands.add_parser(
        'send', help='send one value to a Zabbix trapper item')
    send.add_argument('host', help='Zabbix host name')
    send.add_argument('key', help='trapper item key')
    send.add_argument('value', help='value to send')
    send.add_argument('--clock', type=int, default=None,
                      help='value timestamp as epoch seconds '
                           '(default: server receive time)')
    send.set_defaults(func=cmd_send)

    provision = commands.add_parser(
        'provision', help='create or update templates, items, triggers and '
                          'hosts from YAML specs')
    provision.add_argument('--templates', metavar='DIR', default=None,
                           help='template spec directory '
                                '(default: config/templates)')
    provision.add_argument('--dry-run', action='store_true',
                           help='print the planned changes without '
                                'applying them')
    provision.set_defaults(func=cmd_provision)

    collect = commands.add_parser(
        'collect', help='sample host metrics with psutil and send them to '
                        'Zabbix trapper items')
    mode = collect.add_mutually_exclusive_group()
    mode.add_argument('--once', action='store_true',
                      help='send one batch and exit')
    mode.add_argument('--interval', metavar='SECONDS', type=int, default=30,
                      help='seconds between batches (default: 30)')
    collect.add_argument('--host', metavar='NAME', default=None,
                         help='Zabbix host the values belong to '
                              '(default: mp-collector)')
    collect.set_defaults(func=cmd_collect)
    return parser


def cmd_send(args):
    from monplat import config
    from monplat.zabbix import sender

    try:
        cfg = config.load()
        zbx = cfg.get('zabbix') or {}
        server = zbx.get('server', 'localhost')
        port = int(zbx.get('port', 10051))
        result = sender.send(server, port,
                             [(args.host, args.key, args.value, args.clock)])
    except (config.ConfigError, sender.SenderError) as exc:
        sys.stderr.write('mpctl send: %s\n' % exc)
        return 1
    print('processed: %(processed)d; failed: %(failed)d; total: %(total)d'
          % result)
    return 0 if result['failed'] == 0 else 1


def cmd_provision(args):
    import requests
    from pyzabbix import ZabbixAPIException

    from monplat import config, templates
    from monplat.zabbix import api

    try:
        specs = templates.load_specs(args.templates or templates.DEFAULT_DIR)
        for spec in specs:
            templates.validate(spec)
    except templates.SpecError as exc:
        sys.stderr.write('mpctl provision: %s\n' % exc)
        return 2
    if not specs:
        sys.stderr.write('mpctl provision: no *.yml specs in %s\n'
                         % (args.templates or templates.DEFAULT_DIR))
        return 2

    totals = dict((action, 0) for action in templates.ACTIONS)
    try:
        cfg = config.load()
        zapi = api.connect(cfg)
        for spec in specs:
            changes = templates.plan(zapi, spec)
            for change in changes:
                print('%-9s %-11s %s' % (change.action, change.obj,
                                         change.name))
            if not args.dry_run:
                templates.apply(zapi, changes)
            for action, count in templates.summary(changes).items():
                totals[action] += count
    except (config.ConfigError, api.ZabbixUnavailable, ZabbixAPIException,
            requests.RequestException, templates.SpecError) as exc:
        sys.stderr.write('mpctl provision: %s\n' % exc)
        return 1
    print('%screated: %d; updated: %d; unchanged: %d'
          % ('dry run, nothing applied: ' if args.dry_run else '',
             totals['create'], totals['update'], totals['unchanged']))
    return 0


def cmd_collect(args):
    from monplat import collector, config, templates
    from monplat.zabbix import sender

    try:
        cfg = config.load()
        keys = collector.template_keys()
    except (config.ConfigError, templates.SpecError) as exc:
        sys.stderr.write('mpctl collect: %s\n' % exc)
        return 2 if isinstance(exc, templates.SpecError) else 1
    try:
        result = collector.run(cfg, once=args.once, interval=args.interval,
                               host=args.host, keys=keys)
    except sender.SenderError as exc:
        sys.stderr.write('mpctl collect: %s\n' % exc)
        return 1
    except KeyboardInterrupt:
        return 0
    print('processed: %(processed)d; failed: %(failed)d; total: %(total)d'
          % result)
    return 0 if result['failed'] == 0 else 1


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    func = getattr(args, 'func', None)
    if func is None:
        parser.print_help()
        return 0
    return func(args)


if __name__ == '__main__':
    sys.exit(main())
