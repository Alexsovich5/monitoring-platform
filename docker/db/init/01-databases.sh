#!/bin/bash
# Runs once, when the postgres data directory is initialised.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" <<-SQL
    CREATE ROLE zabbix LOGIN PASSWORD 'zabbix';
    CREATE DATABASE zabbix OWNER zabbix ENCODING 'UTF8' TEMPLATE template0;
    CREATE ROLE monplat LOGIN PASSWORD 'monplat';
    CREATE DATABASE monplat OWNER monplat ENCODING 'UTF8' TEMPLATE template0;
SQL

# The monplat tables, owned by the monplat role.  /sql is ./sql from the
# repository, mounted read-only by docker-compose.yml.
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname monplat <<-SQL
    SET ROLE monplat;
    \i /sql/monplat_schema.sql
SQL
