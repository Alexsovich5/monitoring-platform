#!/bin/sh
set -e
cd /app && python setup.py -q develop --no-deps >/dev/null && exec "$@"
