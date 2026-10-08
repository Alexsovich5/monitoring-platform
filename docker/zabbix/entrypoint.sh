#!/bin/bash
# Wait for PostgreSQL, load the Zabbix schema on first start, then hand
# over to supervisord.
set -e

export PGHOST="${DB_HOST:-db}" PGUSER="${DB_USER:-zabbix}" \
       PGPASSWORD="${DB_PASSWORD:-zabbix}" PGDATABASE="${DB_NAME:-zabbix}"

for i in $(seq 1 90); do
    if psql -tAc 'SELECT 1' >/dev/null 2>&1; then
        break
    fi
    if [ "$i" = 90 ]; then
        echo "database $PGDATABASE on $PGHOST not reachable" >&2
        exit 1
    fi
    sleep 2
done

has_users=$(psql -tAc "SELECT 1 FROM information_schema.tables
                       WHERE table_schema = 'public' AND table_name = 'users'")
if [ "$has_users" != 1 ]; then
    echo "loading Zabbix schema into $PGDATABASE"
    for f in schema images data; do
        psql -q -v ON_ERROR_STOP=1 -f "/usr/share/zabbix-server-pgsql/$f.sql" >/dev/null
    done
fi

# Recheck unsupported items every 30 s instead of the stock 600 s, so an
# agent item such as vfs.file.size on a file that was missing comes back
# soon after the file reappears.
psql -q -v ON_ERROR_STOP=1 -c 'UPDATE config SET refresh_unsupported = 30' >/dev/null

mkdir -p /var/run/zabbix /var/log/zabbix
chown zabbix:zabbix /var/run/zabbix /var/log/zabbix
# The monplat-secrets volume holds the alert intake token that "mpctl
# provision" writes; the alertscript runs as zabbix and needs to read it.
mkdir -p /var/lib/monplat/secrets
chgrp zabbix /var/lib/monplat/secrets
chmod 2750 /var/lib/monplat/secrets
if [ -f /var/lib/monplat/secrets/intake.token ]; then
    chgrp zabbix /var/lib/monplat/secrets/intake.token
    chmod 0640 /var/lib/monplat/secrets/intake.token
fi

mkdir -p /var/spool/mp-demo
chown zabbix:zabbix /var/spool/mp-demo
chmod 1777 /var/spool/mp-demo

exec /usr/bin/supervisord -c /etc/supervisor/supervisord.conf
