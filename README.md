# monitoring-platform

A Zabbix 2.4.3 server, backed by PostgreSQL 9.3, with a Python 2.7 toolkit
(`monplat`, run as `mpctl`) around it. The toolkit provisions templates,
triggers and hosts from YAML, pushes host metrics with psutil over the Zabbix
sender protocol, and serves Zabbix history over a small Flask API with
Graphite-compatible endpoints so that Grafana 1.9.1 can plot it. It also
records Zabbix alerts in PostgreSQL, forwards them to a Pushover-style push
API, runs Zabbix global scripts for matching problems, and forecasts how many
hours remain before a filesystem reaches its threshold.

Personal project built on the 2014-era stack (Zabbix 2.4.3, Python 2.7, PostgreSQL 9.3, Grafana 1.9.1).

## Status

**Implemented**

- Zabbix 2.4.3 server, PHP frontend and agent on Ubuntu 14.04 against
  PostgreSQL 9.3, run under supervisord: `docker/zabbix/Dockerfile`,
  `docker-compose.yml`; tested by `tests/integration/test_zabbix_up.py`.
- YAML configuration with `MONPLAT_<SECTION>_<KEY>` environment overrides:
  `monplat/config.py`; tested by `tests/unit/test_config.py`.
- Zabbix sender protocol (`ZBXD\x01` framing) in Python and `mpctl send`,
  with a 64 KiB cap on the reply and one deadline for the whole exchange:
  `monplat/zabbix/sender.py`; tested by `tests/unit/test_sender.py` against
  in-process fake trappers (normal, oversized, endless and trickling replies).
- Templates, applications, items, triggers, hosts and template links
  provisioned idempotently from `config/templates/*.yml` with
  `mpctl provision`: `monplat/templates.py`, `monplat/zabbix/api.py`,
  `monplat/cli.py`; tested by `tests/unit/test_templates.py`,
  `tests/unit/test_zabbix_api.py`, `tests/unit/test_cli.py` and
  `tests/integration/test_provision.py` (a second run changes nothing).
- Host metric collection with psutil, pushed to trapper items by
  `mpctl collect`: `monplat/collector.py`; tested by
  `tests/unit/test_collector.py` and `tests/integration/test_collect.py`.
- SNMP v2c polling of sysUpTime and interface counters from a simulated
  device: `config/templates/mp-snmp.yml`, `docker/snmpsim/data/public.snmprec`;
  tested by `tests/unit/test_templates_snmp.py` and
  `tests/integration/test_snmp.py`.
- Zabbix history read from PostgreSQL with time bucketing:
  `monplat/history.py`; tested by `tests/unit/test_history.py` and
  `tests/integration/test_history_db.py`.
- REST API for health, hosts, items, item history and events:
  `monplat/api/app.py`, `monplat/timeparse.py`; tested by
  `tests/unit/test_api.py`, `tests/unit/test_timeparse.py` and
  `tests/integration/test_api_http.py`.
- Graphite-compatible `/metrics/find` and `/render` (JSON, wildcards,
  `alias()`): `monplat/api/graphite.py`; tested by
  `tests/unit/test_graphite.py` and `tests/integration/test_graphite_http.py`.
- Grafana 1.9.1 served statically, with dashboards generated from the template
  specs by `mpctl dashboards`: `monplat/dashboards.py`,
  `docker/grafana/Dockerfile`, `grafana/dashboards/`; tested by
  `tests/unit/test_dashboards.py` and `tests/integration/test_grafana.py`.
- Alertscript that posts Zabbix PROBLEM/OK notifications to the API, which
  stores them in the `monplat` database; the media type and action are created
  by `mpctl provision`: `docker/zabbix/alertscripts/mp_alert.py`,
  `monplat/events.py`, `monplat/actions.py`, `sql/monplat_schema.sql`; tested
  by `tests/unit/test_mp_alert.py`, `tests/unit/test_events.py`,
  `tests/unit/test_actions.py` and `tests/integration/test_alert_chain.py`.
  Event intake needs a shared token that `mpctl provision` generates into the
  `monplat-secrets` volume (never committed) and the alertscript sends as
  `X-Monplat-Token`; the message body is parsed strictly, with `value=` last
  so item values cannot inject fields: `monplat/intake.py`; tested by
  `tests/unit/test_intake.py`, `tests/unit/test_events.py` and
  `tests/integration/test_alert_chain.py`.
- Push notifications to a Pushover-compatible API with severity-to-priority
  mapping: `monplat/notify.py`, `tests/stubs/pushover_stub.py`; tested by
  `tests/unit/test_notify.py` and `tests/unit/test_pushover_stub.py`.
- Auto-remediation: opt-in rules in `config/remediation.yml` run Zabbix
  global scripts through `script.execute` once the Zabbix API confirms the
  event as a current, unacknowledged PROBLEM on a host in the rule's allow
  list, with a per-host, per-rule cooldown; runs and refusals are both
  recorded: `monplat/remediation.py`; tested by
  `tests/unit/test_remediation.py` and `tests/integration/test_remediation.py`
  (with `tests/integration/remediation.yml`, which enables the rule).
- Predictive alerting: a least-squares fit over recent history gives the hours
  left before a threshold, sent to a trapper item that a trigger watches
  (`mpctl forecast`): `monplat/forecast.py`; tested by
  `tests/unit/test_forecast.py` and `tests/integration/test_forecast.py`.

**Not implemented / known limitations**

- SNMP devices are simulated with snmpsim; Pushover is a local stub; no real push is sent. The monitored servers are the stack's own containers; no real servers are monitored.
- No WMI monitoring of Windows hosts; there is no Windows host to test
  against.
- No performance optimisation tools; the idea was never defined beyond a
  title and cannot be tested without a real workload.
- No native mobile app or SMS gateway; mobile notification is the one
  Pushover-compatible push API, and only against the local stub.
- No RPM/yum and systemd install path; the Zabbix packages, schema load and
  service start are done in the Zabbix image build and entrypoint.
- Dashboards cannot be saved from the Grafana UI. Grafana 1.x stores them in
  Elasticsearch or InfluxDB, neither of which is part of this stack; the
  dashboards are generated JSON files loaded from disk.
- No Grafana Zabbix datasource plugin; the Graphite-compatible endpoints on
  the API replace it.
- No high availability, Zabbix proxies or distributed monitoring: one server
  and one database.
- The Graphite endpoints implement only `find` and JSON `render` with
  wildcards and `alias()`; other Graphite functions and output formats are
  rejected.
- The capacity forecast is a single-window linear least-squares fit; it does
  not model seasonality.
- Remediation is off by default; set `enabled: true` on a rule in
  `config/remediation.yml` (or point `MONPLAT_REMEDIATION_RULES_FILE` at
  another file) and rerun `mpctl provision`. It only runs pre-registered
  Zabbix global scripts, and it needs `EnableRemoteCommands=1` on the agent
  (set in `docker/zabbix/zabbix_agentd.conf`), which also lets the `app` test
  runner send remote commands to that agent. That is a security trade-off that
  is acceptable only in this lab.
- The monplat API has no TLS. Only event intake is authenticated; the
  read-only endpoints are open so Grafana can call them, and `/render` does
  not limit how many series or how long a range it reads. Published ports
  listen on 127.0.0.1 only.
- supervisord's XML-RPC port in the `zabbix` container has no password; it is
  reachable only on the compose network.
- The Zabbix 2.4.3 packages and the Grafana source tarball are pinned by
  SHA-256 sums recorded from an HTTPS download (`docker/zabbix/SHA256SUMS`,
  `docker/grafana/SHA256SUMS`), since no signed index covers them. Trusty
  packages come from the Ubuntu archive over HTTP, checked by apt against the
  archive's signed Release files.
- The stack uses a modern `docker compose` file (a `services:` key, no
  `version`) because current Docker no longer reads Fig 1.0 / Compose v1
  files.
- Some images are the oldest pullable tags rather than exact 2014 releases:
  `python:2.7.13` (Python 2.7 on Debian jessie), `postgres:9.3` (9.3.25) and
  `node:0.10` (0.10.48). The `zabbix` and `grafana` images are amd64 only.
- Credentials in `config/monplat.yml` and the compose file are fixed demo
  values.

## Built with

- **Python 2.7** — Flask 0.10.1, Werkzeug 0.9.6, Jinja2 2.7.3, MarkupSafe
  0.23, itsdangerous 0.24, gunicorn 19.1.1, requests 2.5.1, pyzabbix 0.7.2,
  psycopg2 2.5.4, psutil 2.1.3, PyYAML 3.11, pysnmp 4.2.5, pyasn1 0.1.7,
  snmpsim 0.2.4; tests with pytest 2.6.4, py 1.4.26 and mock 1.0.1
  (`requirements.txt`, `requirements-dev.txt`)
- **Zabbix 2.4.3** — server, frontend and agent from the `2.4.3-1+trusty`
  packages on Ubuntu 14.04, with Apache 2.4.7, PHP 5.5.9 and supervisor 3.0b2
- **PostgreSQL 9.3** — `postgres:9.3` image
- **Grafana 1.9.1** — built on Node.js 0.10 with less 1.4.2, ycssmin 1.0.1,
  mkdirp 0.3.5 and mime 1.2.11 (`docker/grafana/package.json`)

## Running it

Everything runs in Docker images from the project's era, so nothing needs installing locally beyond Docker.

```bash
make build                                    # fetch Zabbix packages and Grafana sources, build the images
docker compose up -d db zabbix snmpsim api pushover-stub grafana
docker compose run --rm --no-deps app scripts/wait_for_stack.sh
docker compose run --rm app mpctl provision   # templates, hosts, scripts, action
docker compose up -d collector forecast       # start pushing metrics and forecasts
```

Then open `http://localhost:20730` for the Grafana dashboards. The API listens
on `http://localhost:20750` and the Zabbix frontend on
`http://localhost:20780/zabbix` (Admin / zabbix).

## Tests

```bash
make test                # runs the suite inside the period image
```

Unit tests cover each `monplat` module with a mocked Zabbix API and an
in-process fake trapper; integration tests run against the real Zabbix 2.4.3
server, PostgreSQL, snmpsim, the push stub and Grafana in the compose stack,
but nothing is tested against real servers, network devices or Pushover.

## Layout

```
.
|-- config
|   |-- templates
|   |   |-- mp-linux.yml
|   |   |-- mp-snmp.yml
|   |   `-- mp-spool.yml
|   |-- actions.yml
|   |-- monplat.yml
|   `-- remediation.yml
|-- docker
|   |-- app
|   |   `-- entrypoint.sh
|   |-- db
|   |   `-- init
|   |       `-- 01-databases.sh
|   |-- grafana
|   |   |-- Dockerfile
|   |   |-- SHA256SUMS
|   |   |-- build-css.sh
|   |   |-- config.js
|   |   `-- package.json
|   |-- snmpsim
|   |   `-- data
|   |       `-- public.snmprec
|   `-- zabbix
|       |-- alertscripts
|       |   `-- mp_alert.py
|       |-- Dockerfile
|       |-- SHA256SUMS
|       |-- apache-zabbix.conf
|       |-- entrypoint.sh
|       |-- run-daemon.sh
|       |-- supervisord.conf
|       |-- zabbix.conf.php
|       |-- zabbix_agentd.conf
|       `-- zabbix_server.conf
|-- docs
|   |-- IMPLEMENTATION_PLAN.md
|   `-- SPEC.md
|-- grafana
|   `-- dashboards
|       |-- mp-linux.json
|       |-- mp-snmp.json
|       `-- mp-spool.json
|-- monplat
|   |-- api
|   |   |-- __init__.py
|   |   |-- app.py
|   |   `-- graphite.py
|   |-- zabbix
|   |   |-- __init__.py
|   |   |-- api.py
|   |   `-- sender.py
|   |-- __init__.py
|   |-- actions.py
|   |-- cli.py
|   |-- collector.py
|   |-- config.py
|   |-- dashboards.py
|   |-- db.py
|   |-- events.py
|   |-- forecast.py
|   |-- history.py
|   |-- intake.py
|   |-- notify.py
|   |-- remediation.py
|   |-- templates.py
|   `-- timeparse.py
|-- scripts
|   |-- tree.py
|   `-- wait_for_stack.sh
|-- sql
|   `-- monplat_schema.sql
|-- tests
|   |-- integration
|   |   |-- __init__.py
|   |   |-- conftest.py
|   |   |-- remediation.yml
|   |   |-- test_alert_chain.py
|   |   |-- test_api_http.py
|   |   |-- test_collect.py
|   |   |-- test_forecast.py
|   |   |-- test_grafana.py
|   |   |-- test_graphite_http.py
|   |   |-- test_history_db.py
|   |   |-- test_provision.py
|   |   |-- test_remediation.py
|   |   |-- test_snmp.py
|   |   `-- test_zabbix_up.py
|   |-- stubs
|   |   |-- __init__.py
|   |   `-- pushover_stub.py
|   |-- unit
|   |   |-- __init__.py
|   |   |-- conftest.py
|   |   |-- test_actions.py
|   |   |-- test_api.py
|   |   |-- test_cli.py
|   |   |-- test_collector.py
|   |   |-- test_config.py
|   |   |-- test_dashboards.py
|   |   |-- test_deploy.py
|   |   |-- test_events.py
|   |   |-- test_forecast.py
|   |   |-- test_graphite.py
|   |   |-- test_history.py
|   |   |-- test_intake.py
|   |   |-- test_mp_alert.py
|   |   |-- test_notify.py
|   |   |-- test_pushover_stub.py
|   |   |-- test_readme.py
|   |   |-- test_remediation.py
|   |   |-- test_secret_leaks.py
|   |   |-- test_sender.py
|   |   |-- test_templates.py
|   |   |-- test_templates_snmp.py
|   |   |-- test_timeparse.py
|   |   |-- test_tree.py
|   |   `-- test_zabbix_api.py
|   `-- __init__.py
|-- .dockerignore
|-- .gitignore
|-- Dockerfile
|-- Makefile
|-- README.md
|-- docker-compose.yml
|-- pytest.ini
|-- requirements-dev.txt
|-- requirements.txt
`-- setup.py
```
