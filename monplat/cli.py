"""Command line entry point for the monplat toolkit (``mpctl``)."""
import argparse
import sys

from monplat import __version__


def build_parser():
    parser = argparse.ArgumentParser(
        prog='mpctl',
        description='Provision, collect and query the monitoring platform.')
    parser.add_argument('--version', action='version', version=__version__)
    parser.add_subparsers(dest='command', title='commands')
    return parser


def main(argv=None):
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    parser.print_help()
    return 0


if __name__ == '__main__':
    sys.exit(main())
