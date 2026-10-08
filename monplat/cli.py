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
