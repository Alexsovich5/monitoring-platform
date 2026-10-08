# monitoring-platform — Implementation Plan

Each task is one commit and leaves `make test` green. Tasks are listed in
dependency order. See `docs/SPEC.md` for the interfaces, schemas and pinned
versions that are referenced here.

Conventions used in every task:

- `app`: compose service built from the root `Dockerfile` (`FROM python:2.7.13`).
  It is the test runner and CLI container. `api`, `collector`, `forecast`,
  `snmpsim` and `pushover-stub` reuse the same image with different commands.
- `make unit` runs `docker compose run --rm --no-deps app py.test -q tests/unit`
  (`--no-deps`, so unit tests never start db/zabbix once `app` depends on them).
- `make integration` runs `make down`, then `docker compose up -d <stack>`
  (every service except `collector` and `forecast`), then
  `scripts/wait_for_stack.sh`, then (from T5 on)
  `docker compose run --rm app mpctl provision` as the single provisioning
  step, then (from T6/T15 on, once those services exist)
  `docker compose up -d collector forecast`, then
  `docker compose run --rm app py.test -q -m integration tests/integration`.
  Nothing else provisions the shared template or `mp-collector`, so no two
  runs race on `template.create`/`host.create`.
- `make down` runs `docker compose down -v`.
- Tests are written first in every task, and each is seen to fail before the
  code that makes it pass.

---

## T1 — Scaffold Docker image, compose, Makefile and pytest harness

- **Goal:** `make test` runs a real (tiny) unit test suite inside the period
  Python image from the first commit.
- **Files:**
  - create `Dockerfile` (`FROM python:2.7.13`; **no `apt-get`**: the jessie indexes are 404 now, and gcc, git and `libpq-dev` already come from the `buildpack-deps` layers; `pip install -r requirements.txt -r requirements-dev.txt`; `ENTRYPOINT ["docker/app/entrypoint.sh"]`, no `pip install -e .` at build time because the bind mount would hide its egg-info)
  - create `docker/app/entrypoint.sh` (`cd /app && python setup.py -q develop --no-deps >/dev/null && exec "$@"`, so the `mpctl` console script and `monplat.egg-info` exist in the mounted tree in every container)
  - create `docker-compose.yml` (Compose-spec file with a `services:` key and no `version` key, since current `docker compose` rejects the legacy v1 format; service `app` only (image `monitoring-platform:app`), repo bind-mounted at `/app`; top-level `name: monitoring-platform` and a default network pinned to subnet `172.47.0.0/24` so the project stays in its own address range on a shared Docker host; any host ports published by later tasks are taken from 20700–20799)
  - create `Makefile` (`build`, `unit`, `test: unit`, `down`, `shell`)
  - create `requirements.txt`: Flask 0.10.1, Werkzeug 0.9.6, Jinja2 2.7.3, MarkupSafe 0.23, itsdangerous 0.24, gunicorn 19.1.1, requests 2.5.1, pyzabbix 0.7.2, psycopg2 2.5.4, psutil 2.1.3, PyYAML 3.11, pysnmp 4.2.5, pyasn1 0.1.7 (all `==`)
  - create `requirements-dev.txt`: pytest 2.6.4, py 1.4.26, mock 1.0.1, snmpsim 0.2.4
  - create `setup.py` (package `monplat`, console script `mpctl = monplat.cli:main`)
  - create `pytest.ini` (declares the `integration` marker; `testpaths` is not available in pytest 2.6, so tests are run by explicit path)
  - create `monplat/__init__.py` (`__version__ = '0.1.0'`), `monplat/cli.py` (argparse, `--version`, no subcommands yet)
  - create `tests/__init__.py`, `tests/unit/__init__.py`, `tests/unit/test_cli.py`
  - create `.gitignore` (includes `*.egg-info`), `.dockerignore`
- **Tests first:** `test_cli.py`: `main(['--version'])` prints `0.1.0` and exits 0; an unknown subcommand exits 2; `subprocess` run of the installed `mpctl --version` console script prints `0.1.0` (catches a missing egg-info / `DistributionNotFound`).
- **Acceptance:** `make build && make test`
- **Commit:**
  ```
  Scaffold Python 2.7 project with Docker, Makefile and pytest

  Adds the period app image, a compose file with a test-runner service,
  pinned requirements and a minimal mpctl entry point with tests.
  ```

## T2 — Add YAML config loader with environment overrides

- **Goal:** One config source for every component (`monplat.config.load`).
- **Files:** create `monplat/config.py` and `config/monplat.yml` (the full example from SPEC). Create `tests/unit/test_config.py`.
- **Tests first:**
  - default path comes from `MONPLAT_CONFIG`
  - `MONPLAT_ZABBIX_URL` overrides `zabbix.url`
  - numeric overrides such as `MONPLAT_ZABBIX_PORT` are cast to int
  - a missing file raises `ConfigError`
  - unknown sections are kept as they are
- **Acceptance:** `make test`
- **Commit:**
  ```
  Add YAML config loader with environment overrides

  Components read config/monplat.yml; MONPLAT_<SECTION>_<KEY> variables
  override individual values for container deployments.
  ```

## T3 — Implement Zabbix sender protocol in Python

- **Goal:** Push trapper values without the `zabbix_sender` binary.
- **Files:**
  - create `monplat/zabbix/__init__.py` and `monplat/zabbix/sender.py`
  - modify `monplat/cli.py` to add `mpctl send HOST KEY VALUE [--clock]`
  - create `tests/unit/test_sender.py` (fake trapper: a thread with a `socket` server that records the request and answers with a canned ZBXD response)
- **Tests first:**
  - `encode()` produces `ZBXD\x01` + an 8-byte little-endian length + JSON with `request: "sender data"` and `clock`
  - `decode_response()` parses `processed: 2; failed: 1; total: 3; seconds spent: 0.0001`
  - a bad header raises `SenderError`
  - `send()` round trip against the fake server
  - `mpctl send` exits 1 when nothing is listening
- **Acceptance:** `make test`
- **Commit:**
  ```
  Implement Zabbix sender protocol for trapper items

  Encodes ZBXD v1 frames, parses the processed/failed summary and exposes
  a one-off `mpctl send` command for debugging.
  ```

## T4 — Add Zabbix 2.4.3 and PostgreSQL 9.3 to the compose stack

- **Goal:** A real Zabbix server, frontend/API and agent running against PostgreSQL 9.3, plus a first integration test.
- **Files:**
  - create `docker/zabbix/Dockerfile`:
    - `FROM ubuntu:14.04`
    - fetch the `zabbix-server-pgsql`, `zabbix-frontend-php`, `zabbix-agent` and `php5-pgsql` dependencies. The `2.4.3-1+trusty` `.deb`s are downloaded explicitly from `repo.zabbix.com/zabbix/2.4/ubuntu/pool/main/z/zabbix/` and installed with `dpkg -i` + `apt-get -f install`. Pull the debs by exact filename, because the repo index only lists the newest 2.4.x.
    - `ENV DEBIAN_FRONTEND=noninteractive`; before installing, preseed `dbconfig-common` off: `echo 'zabbix-server-pgsql zabbix-server-pgsql/dbconfig-install boolean false' | debconf-set-selections` and the same line for `zabbix-frontend-php`, because both debs depend on `dbconfig-common` and would otherwise try to configure a database during `docker build`. `dbconfig-common` is installed before the debs, and `/etc/dbconfig-common/zabbix-server-pgsql.conf` is pre-written with `dbc_install='false'`: the server package's config step otherwise resets the debconf answer to `true` (the frontend's preseed alone is enough)
    - the build fails unless `dpkg-query` reports all three packages as `1:2.4.3-1+trusty install ok installed`
    - install supervisor and postgresql-client
  - create `docker/zabbix/entrypoint.sh`. It waits for `db`, loads `/usr/share/zabbix-server-pgsql/schema.sql`, `images.sql` and `data.sql` (plain files in 2.4.3) if the `users` table is missing, runs `mkdir -p /var/spool/mp-demo && chown zabbix:zabbix /var/spool/mp-demo && chmod 1777 /var/spool/mp-demo` (used in T14), and then runs `exec supervisord`.
  - create `docker/zabbix/run-daemon.sh <binary> <conf> <pidfile>`. Zabbix 2.4.3 daemons have no foreground flag (`-f` arrived in 3.0) and always fork, and supervisor 3.0b2's `pidproxy` exits as soon as its direct child (the forking parent) exits, so it cannot be used. The wrapper runs `<binary> -c <conf>`, waits up to 30 s for `<pidfile>` to appear, traps TERM/INT to `kill $(cat <pidfile>)`, then loops `while kill -0 $(cat <pidfile>) 2>/dev/null; do sleep 5; done; exit 1`. It stays in the foreground while the daemon lives, forwards stop signals, and exits non-zero when the daemon dies so supervisord restarts it.
  - create `docker/zabbix/supervisord.conf` (`nodaemon=true`; `[inet_http_server] port=0.0.0.0:9001`, not published, so the test runner can read the process table over XML-RPC): `[program:zabbix_server]` runs `/usr/local/bin/run-daemon.sh /usr/sbin/zabbix_server /etc/zabbix/zabbix_server.conf /var/run/zabbix/zabbix_server.pid`, `[program:zabbix_agentd]` runs `/usr/local/bin/run-daemon.sh /usr/sbin/zabbix_agentd /etc/zabbix/zabbix_agentd.conf /var/run/zabbix/zabbix_agentd.pid`, and `[program:apache2]` runs `/bin/bash -c 'source /etc/apache2/envvars && mkdir -p "$APACHE_RUN_DIR" "$APACHE_LOCK_DIR" "$APACHE_LOG_DIR" && exec /usr/sbin/apache2 -DFOREGROUND'`. The Dockerfile copies `run-daemon.sh` to `/usr/local/bin/` with mode 0755.
  - create `docker/zabbix/zabbix_server.conf` (`DBHost=db`, `PidFile=/var/run/zabbix/zabbix_server.pid`, `AlertScriptsPath=/usr/lib/zabbix/alertscripts`, `StartSNMPTrapper=0`, `CacheUpdateFrequency=5` so newly provisioned items accept trapper values within seconds instead of up to 60 s)
  - create `docker/zabbix/zabbix_agentd.conf` (`Server=zabbix,127.0.0.1,app`: `app` is the test runner, which `make integration` starts with `docker compose run --use-aliases` so the name resolves to it; the 2.4 agent resolves hostnames per connection but has no CIDR ranges in `Server`; `Hostname=Zabbix server`, `PidFile=/var/run/zabbix/zabbix_agentd.pid`)
  - create `docker/zabbix/zabbix.conf.php` (pre-seeded, which skips the setup wizard)
  - create `docker/zabbix/apache-zabbix.conf` (sets `php_value date.timezone UTC`)
  - create `docker/db/init/01-databases.sh`. It creates the roles and databases `zabbix` and `monplat`.
  - modify `docker-compose.yml` to add `db` (`postgres:9.3`, init scripts mounted at `/docker-entrypoint-initdb.d`, named volume `dbdata`) and `zabbix` (host ports `20780:80` and `20751:10051`, inside this project's 20700–20799 range; `platform: linux/amd64`, because the 2.4.3 trusty debs exist only for amd64 and i386); `app` gains `depends_on: [db, zabbix]`
  - create `scripts/wait_for_stack.sh`. It polls `apiinfo.version` via curl for up to 180 s (`WAIT_TIMEOUT`). It runs inside the `app` container (`docker compose run --rm --no-deps app scripts/wait_for_stack.sh`), so it uses `http://zabbix/zabbix` on the compose network.
  - create `monplat/zabbix/api.py` (`connect()` with retry, `get_or_create()`)
  - modify `Makefile` so that `test: unit integration` and `integration` exist (`build` builds `app` and `zabbix`; the pytest run of `integration` uses `docker compose run --rm --no-deps --use-aliases app ...`)
  - create `tests/integration/__init__.py`, `tests/integration/conftest.py` (session fixtures `cfg`, `zapi`; helper `send_until_processed(cfg, rows, timeout=60)` that retries `sender.send` until `processed == total`; it does not trigger `zabbix_server -R config_cache_reload`, because the test runner cannot exec into the `zabbix` container and `CacheUpdateFrequency=5` already bounds the wait; helpers `supervisor_processes()` (supervisord XML-RPC `getAllProcessInfo`) and `agent_get(key)` (a passive agent check, like `zabbix_get`)), `tests/integration/test_zabbix_up.py`
  - create `tests/unit/test_zabbix_api.py`
- **Tests first:**
  - unit: `connect()` retries on `requests.ConnectionError` and gives up after N attempts (pyzabbix mocked)
  - unit: `get_or_create()` returns the existing id without calling `create`
  - integration: `zapi.api_version() == '2.4.3'`
  - integration: `host.get` finds `Zabbix server`
  - integration: the `zapi` fixture enables the stock `Zabbix server` host (disabled in 2.4's `data.sql`) with `host.update status=0` (status only, no `templates` key). Within 120 s, `zabbix_server` has written at least one `history`/`history_uint` row for its agent items (checked via psycopg2).
  - integration: supervisord's process table (the data behind `supervisorctl status`, read over XML-RPC on `zabbix:9001`) shows `zabbix_server`, `zabbix_agentd` and `apache2` all `RUNNING` with at least 30 s uptime (the `run-daemon.sh` wrappers stay in the foreground while the forked daemons live), and the agent's `proc.num[zabbix_server]` and `proc.num[zabbix_agentd]` (exact process-name match, as `pgrep -x`) are both at least 1. The test runner has no Docker CLI or socket, so it cannot `docker exec` `supervisorctl`/`pgrep` in the `zabbix` container.
- **Acceptance:** `make build && make test`
- **Commit:**
  ```
  Add Zabbix 2.4.3 server, frontend and PostgreSQL 9.3 services

  Builds Zabbix from the official trusty packages, seeds the schema on
  first start and adds an API client wrapper with integration tests.
  ```

## T5 — Provision templates, items, triggers and hosts from YAML

- **Goal:** Templates and triggers as code, idempotent (SPEC feature 2).
- **Files:**
  - create `monplat/templates.py` (`load_specs`, `validate`, `plan`, `apply`)
  - create `config/templates/mp-linux.yml` (trapper items for CPU util, load1, mem pused, fs pused `/`, net in/out bytes and `mp.forecast.hours_left[/]`; triggers for high CPU, low memory, FS > 90 % and forecast < horizon; host `mp-collector` declared with `interfaces: []`, because it has only trapper items; Zabbix 2.4.3's `host.create` rejects a host without interfaces (`No interfaces for host`), so `templates.py` creates such a host with a placeholder agent interface `127.0.0.1:10050` that none of its items poll)
  - modify `monplat/cli.py` to add `mpctl provision [--templates] [--dry-run]`
  - host-template links use `host.massadd` with `templates`, never a replacing `host.update templates=[...]`
  - modify `Makefile` so that `integration` runs `docker compose run --rm app mpctl provision` once after `scripts/wait_for_stack.sh` and before pytest (see Conventions)
  - modify `tests/integration/conftest.py` to add a session fixture `test_hosts` that creates (idempotently, after the Makefile's provision step has created Template MP Linux) `mp-test-collect`, `mp-test-alert` and `mp-test-forecast` (trapper-only with the same placeholder agent interface, group MP Servers, linked to Template MP Linux), one per integration module that sends data
  - create `tests/unit/test_templates.py`, `tests/integration/test_provision.py`
- **Tests first:**
  - unit: validation rejects an unknown item type, a missing key, an SNMP item without an OID, and a trigger expression that references an undefined key
  - unit: `plan()` against a mocked API yields `create` for new objects, `update` for a changed trigger expression and `unchanged` otherwise
  - unit: `--dry-run` makes no API writes
  - unit: linking a template to an existing host calls `host.massadd`, and never `host.update` with a `templates` key
  - integration: after the Makefile's setup provision, another `mpctl provision` run reports 0 created / 0 updated, and `template.get` finds `Template MP Linux` and `host.get` finds `mp-collector` (the test does not assume it performs the first run)
  - integration: `mp-collector` is linked to `Template MP Linux`
- **Acceptance:** `make test`
- **Commit:**
  ```
  Provision Zabbix templates and triggers from YAML specs

  mpctl provision validates template specs, plans create/update changes
  against the Zabbix API and applies them idempotently.
  ```

## T6 — Collect host metrics with psutil and push to Zabbix

- **Goal:** Real-time server metric collection (SPEC feature 3).
- **Files:**
  - create `monplat/collector.py`
  - modify `monplat/cli.py` to add `mpctl collect [--once|--interval] [--host]`
  - modify `docker-compose.yml` to add the `collector` service (runs `mpctl collect --interval 30` only, matching SPEC; it never provisions)
  - modify `Makefile` so that `integration` starts `collector` with `docker compose up -d collector` only after the provision step and excludes it from the initial `up -d <stack>`
  - create `tests/unit/test_collector.py` and `tests/integration/test_collect.py`
- **Tests first:**
  - unit: `sample()` maps psutil 2.1 calls (`cpu_percent`, `virtual_memory`, `disk_usage('/')`, `net_io_counters`, `os.getloadavg`) to the template keys, with psutil mocked
  - unit: the payload only contains keys that are defined in the template spec
  - unit: `--once` sends exactly one batch
  - integration: after `send_until_processed` has confirmed the host's items are live, `mpctl collect --once --host mp-test-collect` gives `processed == N`, and `history.get` returns `mp.cpu.util` for `mp-test-collect` within 60 s
- **Acceptance:** `make test`
- **Commit:**
  ```
  Add psutil collector that pushes host metrics to Zabbix

  Samples CPU, memory, filesystem, network and load, sends them as trapper
  values and runs as a compose service on a fixed interval.
  ```

## T7 — Monitor a simulated SNMP device

- **Goal:** SNMP polling by Zabbix against snmpsim (SPEC feature 4).
- **Files:**
  - create `docker/snmpsim/data/public.snmprec` (sysDescr, sysUpTime, ifDescr/ifInOctets/ifOutOctets for 2 interfaces)
  - create `config/templates/mp-snmp.yml` (`Template MP SNMP Device`, `snmpv2` items, host `mp-switch-sim` with an SNMP interface `snmpsim:1161`)
  - modify `docker-compose.yml` to add the `snmpsim` service (`snmpsimd.py --data-dir=/data --agent-udpv4-endpoint=0.0.0.0:1161 --process-user=nobody --process-group=nogroup`; snmpsim 0.2.4 aborts as root without a user/group to drop to, and binds after dropping privileges, hence the unprivileged port). `docker/snmpsim/data` is mounted read-only and its files are world-readable so `nobody` can read them.
  - modify `monplat/templates.py` to support SNMP interfaces and items
  - create `tests/unit/test_templates_snmp.py` and `tests/integration/test_snmp.py`
- **Tests first:**
  - unit: an SNMP item spec maps to Zabbix `type=4`, `snmp_oid` and `snmp_community`
  - unit: the host interface maps to `type=2`, `port 1161`
  - integration: the pysnmp 4.2.5 oneliner API, `from pysnmp.entity.rfc3413.oneliner import cmdgen; cmdgen.CommandGenerator().getCmd(cmdgen.CommunityData('public'), cmdgen.UdpTransportTarget(('snmpsim', 1161)), '1.3.6.1.2.1.1.3.0')`, returns no error indication and a value
  - integration: after provisioning, `item.get` for `sysUpTime` on `mp-switch-sim` has a non-empty `lastvalue` within 120 s
- **Acceptance:** `make test`
- **Commit:**
  ```
  Poll a simulated SNMP device through a provisioned template

  Adds an snmpsim service with a recorded IF-MIB walk and an SNMP v2c
  Zabbix template linked to a simulated switch host.
  ```

## T8 — Read Zabbix history from PostgreSQL with time bucketing

- **Goal:** Fast history reads for the API, Graphite bridge and forecast.
- **Files:** create `monplat/db.py` (a psycopg2 connection helper using the config DSNs), `monplat/history.py`, `tests/unit/test_history.py` and `tests/integration/test_history_db.py`.
- **Tests first:**
  - unit: `bucket()` averages points into `step`-second buckets aligned to the step, drops empty buckets and keeps order
  - unit: `value_type` 0 → `history` and 3 → `history_uint`, and any other type raises
  - integration: send 5 values with explicit clocks for `mp-test-collect` via `send_until_processed` (on a key the collector test does not write, e.g. `mp.load1`, with clocks one day in the past), then `fetch()` over that range returns exactly those values in clock order
- **Acceptance:** `make test`
- **Commit:**
  ```
  Read Zabbix item history directly from PostgreSQL

  Queries history/history_uint by item value type and averages points
  into fixed-width time buckets for charting and forecasting.
  ```

## T9 — Serve hosts, items and history over a Flask REST API

- **Goal:** Metric API (SPEC feature 5).
- **Files:**
  - create `monplat/api/__init__.py` and `monplat/api/app.py` (`create_app`, `/api/v1/health`, `/hosts`, `/hosts/<host>/items`, `/items/<id>/history`, CORS header)
  - create `monplat/timeparse.py` (`parse_time(s, now)`: `now`, epoch seconds, and `-<n><unit>` with Graphite units `s`, `min`, `h`, `d`, `w`, `mon` = 30 d, `y` = 365 d, plus bare `m` as minutes; Grafana 1.9.1's `translateTime` sends `-5min` and `-1mon`)
  - modify `docker-compose.yml` to add the `api` service (`gunicorn -b 0.0.0.0:5000 -w 2 'monplat.api.app:create_app()'`, port 5000 on the compose network, published on host port `20750:5000` inside this project's 20700–20799 range); add `api` to the Makefile's `STACK` so `make integration` starts it
  - create `tests/unit/test_api.py` (Flask test client, history/db mocked), `tests/unit/test_timeparse.py` and `tests/integration/test_api_http.py`
- **Tests first:**
  - unit: health returns 503 when a DB connect raises
  - unit: an unknown host returns 404
  - unit: `parse_time` handles `-5min`, `-15min`, `-6h`, `-7d`, `-1mon`, `-1y`, `-5m`, `now` and an epoch value, and rejects `-5x`
  - unit: bad `from`/`until` and a bad `step` return 400
  - unit: the CORS header is present
  - integration: `requests.get('http://api:5000/api/v1/hosts')` includes `mp-collector`, and history for its CPU item is non-empty
- **Acceptance:** `make test`
- **Commit:**
  ```
  Expose hosts, items and history through a Flask REST API

  Adds a gunicorn-served API with health checks, relative time parsing
  and bucketed item history read from the Zabbix database.
  ```

## T10 — Add Graphite-compatible find and render endpoints

- **Goal:** Let Grafana 1.x's Graphite datasource read Zabbix data (first half of SPEC feature 6).
- **Files:** create `monplat/api/graphite.py`. Modify `monplat/api/app.py` to register `/metrics/find/` and `/render` (GET and form POST). Create `tests/unit/test_graphite.py` and `tests/integration/test_graphite_http.py`.
- **Tests first:**
  - unit: `metric_path('mp-collector', 'mp.fs.pused[/]') == 'zabbix.mp-collector.mp_fs_pused_'`
  - unit: collisions get an `_<itemid>` suffix
  - unit: `find('zabbix.*')` returns expandable host nodes, and `find('zabbix.mp-collector.*')` returns leaves
  - unit: `alias(zabbix.h.k, "CPU")` renames the target
  - unit: `maxDataPoints` reduces points via `bucket()`
  - unit: datapoints are `[value, ts]` pairs
  - unit: `format=png` returns 400
  - unit: `/render` accepts `from=-15min` (what the Grafana time picker sends)
  - integration: `POST /render` with `target=zabbix.mp-collector.mp_cpu_util&from=-1h&format=json` returns at least one non-null datapoint
- **Acceptance:** `make test`
- **Commit:**
  ```
  Add Graphite find and render endpoints over Zabbix history

  Maps hosts and item keys to sanitised Graphite paths and serves the
  JSON render subset that Grafana 1.x's Graphite datasource uses.
  ```

## T11 — Serve Grafana 1.9.1 with generated dashboards

- **Goal:** Grafana visualisation (second half of SPEC feature 6).
- **Files:**
  - create `docker/grafana/Dockerfile`:
    - `FROM node:0.10`
    - copy the Grafana v1.9.1 source tarball and the npm tarballs from `docker/grafana/vendor/`. `make grafana-vendor` fills that directory (gitignored) with the host's `curl`: `github.com/grafana/grafana/archive/v1.9.1.tar.gz` and `npm pack`-equivalent registry tarballs of less 1.4.2, ycssmin 1.0.1, mkdirp 0.3.5 and mime 1.2.11. This avoids TLS failures from node 0.10.48's OpenSSL 1.0.1 and old CA list against today's GitHub and npm certificate chains. `make build` depends on `grafana-vendor`.
    - `npm install --no-optional ./vendor/*.tgz` (offline, versions as pinned in `docker/grafana/package.json`; `--no-optional` keeps less 1.4.2's open-range `request >=2.12.0` from being fetched)
    - run `lessc --yui-compress` (the less 1.4 compress flag, as Grafana 1.9.1's grunt-contrib-less ~0.7.0 used) to compile `bootstrap.dark/light` and `grafana-responsive` into `src/css/*.min.css` and concatenate `grafana.dark.min.css` / `grafana.light.min.css` exactly as in `tasks/options/concat.js`
    - serve `src/` with `python -m SimpleHTTPServer 3000`
  - modify `Makefile` (add `grafana-vendor`; `build` depends on it) and `.gitignore` (add `docker/grafana/vendor/`)
  - create `docker/grafana/package.json` (less 1.4.2, ycssmin 1.0.1, mkdirp 0.3.5, mime 1.2.11, all exact)
  - create `docker/grafana/config.js` (Graphite datasource `http://localhost:20750`, the api's host port, since the browser makes the requests; `default_route: '/dashboard/file/mp-linux.json'`)
  - create `monplat/dashboards.py`
  - modify `monplat/cli.py` to add `mpctl dashboards`
  - create `grafana/dashboards/mp-linux.json` and `grafana/dashboards/mp-snmp.json` (generated and committed; mounted into `src/app/dashboards/`)
  - modify `docker-compose.yml` to add the `grafana` service (port 3000 on the compose network, published on host port `20730:3000` inside this project's 20700–20799 range; `platform: linux/amd64` for `node:0.10`; `grafana/dashboards` mounted read-only at `src/app/dashboards/`); add `grafana` to the Makefile's `STACK`
  - create `tests/unit/test_dashboards.py` and `tests/integration/test_grafana.py`
- **Tests first:**
  - unit: `build(spec)` returns `version: 6` (Grafana 1.9.1 dashboardSrv's schema, so no client-side migration), one row per application and one graph panel per numeric item, whose target is the Graphite path from T10
  - unit: the output is deterministic (sorted keys), so the committed JSON matches regeneration
  - integration: `grafana:3000/index.html`, `css/grafana.dark.min.css` and `app/dashboards/mp-linux.json` all return 200, and the dashboard's targets resolve via `/metrics/find`
- **Acceptance:** `make build && make test`
- **Commit:**
  ```
  Serve Grafana 1.9.1 with dashboards generated from templates

  Builds Grafana's CSS with less 1.4.2, points its Graphite datasource at
  the API and generates one dashboard per template spec.
  ```

## T12 — Record Zabbix alerts through an alertscript and event log

- **Goal:** Alert intake (SPEC feature 7).
- **Files:**
  - create `sql/monplat_schema.sql` (`events`, `remediations`)
  - modify `docker/db/init/01-databases.sh` to apply the schema to `monplat` (as role `monplat`), and `docker-compose.yml` to mount `./sql` read-only at `/sql` in `db`
  - create `docker/zabbix/alertscripts/mp_alert.py` (stdlib `urllib`/`urllib2` only, runs on trusty's Python 2.7.6, and logs failures to stderr)
  - modify `docker/zabbix/Dockerfile` to copy the alertscript and make it executable
  - create `monplat/events.py`
  - modify `monplat/api/app.py` to add `POST/GET /api/v1/events`
  - create `config/actions.yml` (media type "MP API", Admin user media, action "MP notify API" with the message template from SPEC, condition Trigger value = PROBLEM (`conditiontype` 5, `operator` 0, `value` 1), `recovery_msg=1`)
  - create `monplat/actions.py` to plan the media type, media and action idempotently (same `Change` objects, applied by `templates.apply`); `mpctl provision` runs it after the templates (`--actions FILE`, default `config/actions.yml`) and counts it in the summary line
  - create `tests/unit/test_events.py`, `tests/unit/test_mp_alert.py`, `tests/unit/test_actions.py` and `tests/integration/test_alert_chain.py`; update the provision CLI tests in `tests/unit/test_templates.py` for the three extra objects
- **Tests first:**
  - unit: `parse_alert` reads the key=value body, maps `{TRIGGER.NSEVERITY}` to an int, and rejects a missing `eventid` with 400
  - unit: a duplicate `(eventid, status)` is stored once
  - unit: `mp_alert.py` builds the right POST body (urllib2 mocked)
  - unit: the provisioned action carries the Trigger value = PROBLEM condition and `recovery_msg=1`
  - integration: send `mp.cpu.util` = 99 for `mp-test-alert` (via `send_until_processed`) → within 120 s, `GET /api/v1/events?host=mp-test-alert&status=PROBLEM` has one entry for the "High CPU" trigger. Then send 5 → exactly one OK event is recorded.
- **Acceptance:** `make build && make test`
- **Commit:**
  ```
  Record Zabbix alerts in PostgreSQL via an alertscript

  Adds a stdlib alertscript, provisions the media type and action, and
  stores PROBLEM/OK events posted to the API in the monplat database.
  ```

## T13 — Forward events as mobile push notifications (stubbed Pushover)

- **Goal:** Mobile notification support (SPEC feature 8).
- **Files:**
  - create `monplat/notify.py`
  - create `tests/stubs/__init__.py` and `tests/stubs/pushover_stub.py` (Flask: `POST /1/messages.json`, `GET /_received`, `DELETE /_received`)
  - modify `monplat/api/app.py` so that the events POST calls `notify.push` and sets `notified`
  - modify `docker-compose.yml` to add `pushover-stub` (port 8025 on the compose network only; no host port), and add `pushover-stub` to `STACK` in the `Makefile`
  - modify `monplat/events.py` to add `claim_notification`/`release_notification`, so a repeated POST of one notification is pushed once and a failed push leaves `notified` false
  - create `tests/unit/test_notify.py` and `tests/unit/test_pushover_stub.py`
  - modify `tests/integration/test_alert_chain.py`
- **Tests first:**
  - unit: severity 0–1 → priority -1, 2–3 → 0 and 4–5 → 1
  - unit: the request carries `token`, `user`, `title`, `message` and `priority` (requests mocked)
  - unit: a non-200 response returns False without raising
  - unit: the stub records messages and rejects a missing token with 400
  - integration: the alert chain now also sees exactly one stub message whose title contains the trigger name
- **Acceptance:** `make test`
- **Commit:**
  ```
  Send event notifications to a Pushover-compatible push API

  Maps Zabbix severity to push priority and adds a local stub service so
  the notification path is exercised end to end without a real account.
  ```

## T14 — Run remediation scripts for matching PROBLEM events

- **Goal:** Auto-remediation (SPEC feature 9).
- **Files:**
  - create `monplat/remediation.py` and `config/remediation.yml` (rule `clear-spool`)
  - modify `monplat/templates.py`/`actions.py` to provision the global script `MP clear spool` via `script.create`/`script.update` (`execute_on` = agent)
  - modify `docker/zabbix/zabbix_agentd.conf` to set `EnableRemoteCommands=1` and `LogRemoteCommands=1`
  - modify `docker-compose.yml` to add a named volume `mp-demo-spool`, mounted at `/var/spool/mp-demo` in both `zabbix` and `app`, so tests can create the file that the remediation removes. The T4 entrypoint already makes the directory `zabbix:zabbix` mode 1777, so the agent (running as `zabbix`) can delete a root-written file regardless of which container mounted the volume first; the test also `chmod 666`s the file it writes.
  - the global script command is `rm -f /var/spool/mp-demo/* && echo cleared`; `remediate` sets `ok=true` only when `script.execute` returns success and the output contains `cleared`, so a permission error is recorded as `ok=false` with its output
  - modify `monplat/api/app.py` so that events POST → `remediate`
  - create `config/templates/mp-spool.yml` (`Template MP Spool`, linked to the existing `Zabbix server` host with `host.massadd`, so Template OS Linux stays linked: agent item `vfs.file.size[/var/spool/mp-demo/blob]` with `delay` 30 and trigger `Spool directory too large on {HOST.NAME}` when `.last()>5242880`, severity warning). The agent key is `vfs.file.size` because Zabbix 2.4 has no `vfs.dir.size`.
  - create `tests/unit/test_remediation.py` and `tests/integration/test_remediation.py`; update the provision CLI tests in `tests/unit/test_templates.py` (one more created object) and `tests/unit/test_dashboards.py` (the new template also yields `grafana/dashboards/mp-spool.json`, generated with `mpctl dashboards`)
  - `config/remediation.yml` carries `command: "rm -f /var/spool/mp-demo/* && echo cleared"` and `expect_output: cleared`; `mpctl provision` gains `--remediation FILE` (default `config/remediation.yml`) and runs `actions.plan_scripts` after the action
  - `remediate` takes the stored `events` row id (`event_id`) for the `remediations` foreign key; the API connects to the Zabbix API only when a rule matches, and records a failed login as an `ok=false` run
  - modify `docker/zabbix/entrypoint.sh` to set `config.refresh_unsupported = 30` on every start. `vfs.file.size` on a missing file makes the item unsupported, which Zabbix only rechecks every 600 s by default; an unsupported item also leaves the trigger in PROBLEM, so after the remediation the test writes an empty `blob` and the item reports 0, which produces the OK event
- **Tests first:**
  - unit: the rule regex and minimum severity match
  - unit: OK events never remediate
  - unit: cooldown blocks a second run inside `cooldown_seconds` (remediations table mocked)
  - unit: a `script.execute` failure, or output without `cleared`, is logged with `ok=false`
  - integration: write a 10 MB `/var/spool/mp-demo/blob` through the shared volume. Within 180 s there is a `remediations` row with `ok=true` for host `Zabbix server`, the file is gone from the shared volume, and, after an empty `blob` is written, a later OK event is recorded.
- **Acceptance:** `make build && make test`
- **Commit:**
  ```
  Remediate matching problems with Zabbix global scripts

  Rules map trigger patterns to provisioned scripts run on the agent via
  script.execute, with a per-host cooldown and an audit table.
  ```

## T15 — Forecast hours until threshold and alert ahead of time

- **Goal:** Predictive alerting (SPEC feature 10).
- **Files:**
  - create `monplat/forecast.py`
  - modify `monplat/cli.py` to add `mpctl forecast [--once|--interval]`
  - modify `docker-compose.yml` to add the `forecast` service
  - modify `Makefile` so that `integration` starts `forecast` together with `collector` after the provision step (`docker compose up -d collector forecast`)
  - modify `config/monplat.yml` (forecast items)
  - create `tests/unit/test_forecast.py` and `tests/integration/test_forecast.py`
- **Tests first:**
  - unit: `fit()` on the exact line y = 2x + 1 gives slope 2 and intercept 1
  - unit: `hours_to_threshold` returns None for a flat or falling series or fewer than 3 points, returns 0 when the threshold is already crossed, and gives the correct hours for a known slope
  - unit: `run(once=True)` sends one trapper value per configured item (sender mocked)
  - integration: send 12 synthetic points (via `send_until_processed`) for `mp-test-forecast` `mp.fs.pused[/]` rising 1 %/h from 70 %, write a temporary config listing that one item (`{host: mp-test-forecast, key: "mp.fs.pused[/]", threshold: 90, ...}`) and run `MONPLAT_CONFIG=<tmp> mpctl forecast --once` (list items cannot be overridden with `MONPLAT_<SECTION>_<KEY>`), and `history.get` of `mp.forecast.hours_left[/]` returns about 9 h (±0.5). Also, the "full within horizon" trigger is in PROBLEM state.
- **Acceptance:** `make test`
- **Commit:**
  ```
  Forecast time to threshold and alert before disks fill

  Fits a least-squares line over recent history, pushes hours-left as a
  trapper value and triggers when it falls inside the horizon.
  ```

## T16 — Regenerate README from the template with honest status

- **Goal:** The final README follows `tools/readme_template.md`.
- **Files:** modify `README.md`.
  - Title and a one-paragraph description.
  - "Personal project built on the 2014-era stack (Zabbix 2.4.3, Python 2.7, PostgreSQL 9.3, Grafana 1.9.1)".
  - Status: each implemented bullet maps to a module plus a test.
  - Not implemented / known limitations: every SPEC out-of-scope item and known limitation, including that the stack uses a modern `docker compose` file because current Docker no longer reads Fig 1.0 / Compose v1 files.
  - A mocked statement: "SNMP devices are simulated with snmpsim; Pushover is a local stub; no real push is sent. The monitored servers are the stack's own containers; no real servers are monitored."
  - Built with: the pinned versions.
  - Running it: `docker compose up -d` for every service except `collector` and `forecast`, then `docker compose run --rm app mpctl provision`, then `docker compose up -d collector forecast` as the smoke sequence (followed by opening `http://localhost:20730`).
  - Tests: a sentence on coverage.
  - Layout: a tree generated by `git ls-files | python scripts/tree.py`.

  Also create `scripts/tree.py` (a small stdlib helper that turns `git ls-files` output into a tree), `tests/unit/test_tree.py` and `tests/unit/test_readme.py`. Commit the README together with the new files, then re-run the tree so that it includes them.
- **Tests first:**
  - `tree.py` renders nested paths deterministically
  - `test_readme.py` checks that the README's Layout block equals `tree.py` run over `git ls-files` (the repo, including `.git`, is bind-mounted into `app`; the `python:2.7.13` image already ships `git`), that every path named in the README exists, that no `{{` placeholders remain, that there are no uptime/percentage business claims, that no `Timeline:` or `Role:` line exists, and that none of the employer/role/timeline values from the root commit's README appear (the test reads them with `git show $(git rev-list --max-parents=0 HEAD):README.md` and extracts the `Timeline`/`Role` values, so the names are never written into the repo)
- **Acceptance:** `git add -A && make test` (staging first so `git ls-files` includes the files this task creates)
- **Commit:**
  ```
  Rewrite README from template with implemented status

  Describes what the stack does, which integrations are simulated, how to
  run and test it, and lists the repository layout from git ls-files.
  ```

## T17 — Security and hygiene hardening

A review of the whole repository after T16. Found and changed:

- **Unbounded trapper reply read** (`monplat/zabbix/sender.py`): `_recv_frame`
  trusted the 64-bit ZBXD length, read until that many bytes or EOF, had only a
  per-`recv` timeout (a trickling peer never timed out) and grew a string with
  `+=`. Now a declared length above `MAX_RESPONSE` (64 KiB) is refused before
  the body is read, `send()`'s `timeout` is one deadline for connect, send and
  every read, and bytes are collected in a `bytearray`. Tests: fake TCP peers
  for an oversized and a huge declared length, an endless stream (read stops at
  the cap) and a 1-byte trickle (fails at the deadline); a reply split over
  many writes still parses.
- **Unauthenticated event intake** (`monplat/api/app.py`): anyone reaching the
  API could post PROBLEM events or forged OK recoveries, and (with the next
  item) trigger remote scripts. New `monplat/intake.py`: `mpctl provision`
  creates a random 256-bit token (mode 0640, group of its directory) in the
  `monplat-secrets` named volume, never in the repository; `mp_alert.py` sends
  it as `X-Monplat-Token`; the API compares it with `hmac.compare_digest` and
  answers 401 (wrong/missing) or 503 (no token file) before reading the body.
  Requests above 64 KiB get 413. The zabbix entrypoint gives the secrets
  directory to the `zabbix` group (mode 2750) so the alertscript can read it;
  the volume is read-only in `api` and absent from the other services. The
  read-only endpoints stay open for Grafana. Published ports now bind to
  127.0.0.1.
- **key=value field injection** (`monplat/events.py`): `{ITEM.VALUE}` and
  trigger names come from monitored hosts, so a value with `"\nstatus=OK"`
  could override fields. `value=` is now the last line of the provisioned
  template and everything after it is the value; other lines must be known
  keys, given once; `eventid`/`trigger_id` digits only, `status`, `severity`,
  `time` validated, `host` restricted to the Zabbix host-name character set
  (the template now sends `{HOST.HOST}` instead of `{HOST.NAME}`); bodies above
  8192 characters are refused; `item_value`/`trigger_name` are cut to their
  column size instead of failing the insert.
- **Forged events ran remote scripts** (`monplat/remediation.py`): the target
  host was looked up by the name in the message. Now the event is re-read with
  `event.get` and `trigger.get`; the script runs only for an existing,
  unacknowledged trigger PROBLEM that is still current, whose trigger id and
  single host match the message and whose Zabbix trigger name and priority
  match the rule, on the hostid reported by Zabbix. Rules are opt-in
  (`enabled`, false in `config/remediation.yml`) and must name `allow_hosts`
  and/or `allow_groups`; the script name must resolve to exactly one global
  script, created with `host_access` 3. Every refusal (including rate limiting
  and Zabbix being unreachable) is recorded in `remediations` with the new
  `ran = false` column, and only real runs start a cooldown. `make
  integration` enables the rule through `MONPLAT_REMEDIATION_RULES_FILE`
  (`tests/integration/remediation.yml`, passed through by compose to `api` and
  `app`); `remediation.rules_file` in `config/monplat.yml` names the default.
- **Password and session token in debug logs**: pyzabbix logs every request at
  DEBUG, including the `user.login` password and the `auth` token.
  `monplat/zabbix/api.py` keeps the `pyzabbix` logger at INFO. A sentinel test
  (`tests/unit/test_secret_leaks.py`) forces login, provision, forecast, DB and
  push failures and checks that the Zabbix password, DB password and push token
  appear in no exception, CLI output, log record or HTTP response.
- **Unverified package downloads**: the Zabbix `.deb`s were fetched over plain
  HTTP with no checksum and installed with `dpkg -i`; the Grafana and npm
  tarballs were fetched over HTTPS without a checksum. `make zabbix-vendor` now
  downloads the debs on the host over HTTPS (like `make grafana-vendor`), and
  both images check `vendor/` against committed `SHA256SUMS` before using it.
  `tests/unit/test_deploy.py` fails if a Dockerfile or apt config uses an
  apt signature bypass or `http://`, if the Makefile downloads over HTTP, if a
  vendored file is not covered by the sums, or if a published port is not on
  127.0.0.1.
- **Alertscript reply read** (`mp_alert.py`): reads at most 64 KiB of the
  reply or error body and reports `IOError`s instead of crashing.

Deviations: T4's Dockerfile no longer fetches the debs with `wget`; they come
from `docker/zabbix/vendor/` (gitignored, filled by `make zabbix-vendor`,
which `make build` depends on). The alert message template field order and
`{HOST.HOST}` differ from the T12 text; SPEC is updated to match.

Not changed (lab trade-offs, listed in the README): supervisord's XML-RPC
port on the compose network has no password; the Zabbix agent accepts remote
commands from `zabbix` and the `app` test runner; `/render` does not limit
the number of targets or the time range; the Zabbix API client reads replies
through `requests` without a size cap; default lab credentials stay in
`config/monplat.yml` and the compose file.

- **Tests first:** the tests named above were seen failing before each
  change; the leak tests for paths that already kept secrets out stay as
  regression guards.
- **Acceptance:** `make build && make test`
- **Commit:**
  ```
  Authenticate event intake and confirm remediation with Zabbix
  ```
