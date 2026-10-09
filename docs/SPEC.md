# monitoring-platform — Specification

Target period: September–December 2014. Every dependency is pinned to a release
published on or before 2014-12-31. Everything builds, runs and tests in Docker.

## Problem

A small team running a handful of Linux servers and SNMP network devices needs
one place that:

- collects server performance metrics continuously (CPU, memory, disk, network,
  load) and SNMP counters from network gear,
- ships them into Zabbix 2.4 with version-controlled templates and triggers
  instead of hand-clicked frontend configuration,
- draws them in Grafana dashboards (Grafana 1.x has no Zabbix datasource, so a
  bridge is needed),
- warns *before* a disk fills up (Zabbix 2.4 has no `forecast()` / `timeleft()`
  trigger functions; they arrived in Zabbix 3.0),
- runs a known fix automatically for known problems, and
- pushes alerts to a phone and exposes the data over a small HTTP API.

This repository rebuilds that as a working solo project: a Python 2.7 toolkit
(`monplat`) around a real Zabbix 2.4.3 server backed by PostgreSQL 9.3, plus a
Grafana 1.9.1 front end.

## In scope

1. **Zabbix 2.4.3 stack in Docker.** A real Zabbix server (PostgreSQL backend),
   the PHP frontend (which serves the JSON-RPC API) and a Zabbix agent, built
   from the official 2.4.3 packages on Ubuntu 14.04, against PostgreSQL 9.3.
2. **Templates and triggers as code.** YAML template specs under
   `config/templates/` are validated and provisioned idempotently through the
   Zabbix API (`mpctl provision`): templates, applications, items, triggers,
   hosts and host-template links. A second run makes no changes.
3. **Real-time server metric collection.** A Python collector (`mpctl collect`)
   reads host metrics with psutil and pushes them to Zabbix trapper items using
   the Zabbix sender protocol (`ZBXD\x01` framing) implemented in Python.
4. **SNMP monitoring.** An SNMP template (sysUpTime, ifInOctets/ifOutOctets) is
   provisioned for a simulated SNMP device, and Zabbix polls it with SNMP v2c.
5. **Metric API.** A Flask HTTP API reads Zabbix history directly from
   PostgreSQL: health, hosts, items, and item history with time bucketing.
6. **Grafana dashboards.** A Graphite-compatible subset (`/metrics/find`,
   `/render`) on the same API lets Grafana 1.9.1's stock Graphite datasource
   plot Zabbix data. `mpctl dashboards` generates Grafana 1.x dashboard JSON
   files from the template specs.
7. **Alert intake and event log.** A Zabbix alertscript (stdlib-only Python)
   posts every PROBLEM/OK notification to the API, which stores it in a
   separate `monplat` PostgreSQL database. `mpctl provision` also creates the media
   type, user media and action that wire this up.
8. **Mobile notifications.** Events are forwarded to a Pushover-compatible push
   API with severity-to-priority mapping. Only a local stub is used (see
   "Simulated/mocked integrations").
9. **Auto-remediation.** Rules in `config/remediation.yml` map trigger name
   patterns and severity to Zabbix global scripts. Rules are opt-in
   (`enabled: false` in the shipped file) and name the hosts or host groups
   they may act on. On a matching PROBLEM event, the API re-reads the event
   and its trigger from the Zabbix API, and only when Zabbix confirms an
   unacknowledged, current PROBLEM on an allowed host does it run the script
   on that host (by the hostid Zabbix reports) through `script.execute`
   (remote commands on the agent), with a per-host/per-rule cooldown. Every
   run and every refusal is logged.
10. **Predictive alerting.** `mpctl forecast` fits a least-squares line to recent
    history of configured items (e.g. `mp.fs.pused[/]`) and pushes
    "hours until threshold" to a trapper item. A provisioned trigger fires when
    the forecast drops below a configured horizon.

## Out of scope

| Original README item | Reason |
|---|---|
| WMI monitoring of Windows hosts | Needs Windows hosts; there is no period-accurate Windows container to test against. |
| Performance optimization tools | Not defined beyond the title in the source README. Tuning tools cannot be tested in a meaningful way without a real production workload. |
| Native mobile app / SMS gateway | Mobile notification is covered by one push API (Pushover-compatible, stubbed). SMS modems and carrier gateways cannot be reproduced. |
| RPM/yum + systemd install path (README "Setup") | Replaced by Docker images. The Zabbix package install, schema load and service start that the README described are done in the Zabbix image's build and entrypoint instead. |
| Grafana dashboard saving from the UI | Grafana 1.x stores dashboards in Elasticsearch/InfluxDB. Dashboards here are generated JSON files loaded from disk. Adding Elasticsearch would bring in a second storage engine outside the README's architecture. |
| Grafana Zabbix datasource plugin | The grafana-zabbix plugin's first release came after 2014. It is replaced by the Graphite-compatible bridge (feature 6). |
| High availability, Zabbix proxies, distributed monitoring | Beyond a solo-project core. One server, one database. |
| Employer/role and timeline lines from the original README | Fabricated, not carried over. The README test (T16) fails if either value reappears. |

## Architecture

```
                         +------------------------------ docker compose ------------------------------+
                         |                                                                             |
  psutil  +-----------+  | ZBXD sender  +-----------------------------+   SQL   +-------------------+ |
 -------->| collector |--+------------->| zabbix (ubuntu:14.04)       |-------->| db (postgres:9.3) | |
          | (monplat) |  |  :10051      |  zabbix_server 2.4.3        |         |  db "zabbix"      | |
          +-----------+  |              |  zabbix_agentd 2.4.3 :10050 |         |  db "monplat"     | |
                         |  SNMP v2c    |  apache2+php5 frontend :80  |         +-------------------+ |
  +-----------+  <-------+--------------|  alertscripts/mp_alert.py   |              ^      ^         |
  | snmpsim   |          |              +-----------------------------+              |      |         |
  | :1161/udp |          |                 |  JSON-RPC API  ^     | HTTP POST        |      |         |
  +-----------+          |  pyzabbix       |  (api_jsonrpc) |     | /api/v1/events   | SQL  | SQL     |
                         |  provision /    v                |     v                  |      |         |
  +-----------+          |  forecast   +-------------------------------------------+ |      |         |
  | mpctl CLI |----------+------------>| api (Flask 0.10 + gunicorn 19.1)           |-+      |         |
  | provision |          |             |  /api/v1/*   /metrics/find  /render        |--------+         |
  | forecast  |          |             |  events -> notify -> remediation           |                  |
  | dashboards|          |             +-------------------------------------------+                   |
  +-----------+          |                  |  Pushover-style POST       ^ Graphite JSON              |
                         |                  v                            |                            |
                         |          +----------------+        +----------------------+                |
                         |          | pushover-stub  |        | grafana 1.9.1 static |<-- browser     |
                         |          | :8025          |        | :3000                |                |
                         |          +----------------+        +----------------------+                |
                         +-----------------------------------------------------------------------------+
```

Components:

| Service | Image | Role |
|---|---|---|
| `db` | `postgres:9.3` | Two databases: `zabbix` (Zabbix 2.4 schema) and `monplat` (events, remediations). |
| `zabbix` | built from `docker/zabbix/Dockerfile` (`ubuntu:14.04`) | zabbix-server-pgsql, zabbix-frontend-php (Apache 2.4 + PHP 5.5) and zabbix-agent 2.4.3, run under supervisord. The 2.4.3 `.deb`s are downloaded on the host over HTTPS by `make zabbix-vendor` and checked against `docker/zabbix/SHA256SUMS` in the build before `dpkg -i`. Zabbix 2.4 daemons have no foreground flag (`-f` arrived in 3.0) and always fork, so supervisord runs each of `zabbix_server` and `zabbix_agentd` through a small wrapper script (`docker/zabbix/run-daemon.sh <binary> <conf> <PidFile>`). The wrapper starts the daemon, traps TERM/INT to `kill $(cat <PidFile>)`, and loops `while kill -0 $(cat <PidFile>) 2>/dev/null; do sleep 5; done; exit 1`, so supervisord keeps a foreground process per daemon, forwards stop signals and restarts a daemon that dies. supervisord runs Apache as `apache2 -DFOREGROUND` after sourcing `/etc/apache2/envvars`. The debs' `dbconfig-common` prompts are preseeded off (`dbconfig-install boolean false`) with `DEBIAN_FRONTEND=noninteractive`. The entrypoint waits for `db`, loads the plain `/usr/share/zabbix-server-pgsql/{schema,images,data}.sql` on first start, and creates `/var/spool/mp-demo` owned by `zabbix` with mode 1777. On every start it also sets `config.refresh_unsupported` to 30 s (stock: 600 s), so an agent item that went unsupported, such as `vfs.file.size` on a missing file, is rechecked within a minute. `zabbix_server.conf` sets `CacheUpdateFrequency=5` so newly provisioned items accept trapper values within seconds (default 60 s). |
| `snmpsim` | `monplat` app image | `snmpsimd.py --data-dir=/data --agent-udpv4-endpoint=0.0.0.0:1161 --process-user=nobody --process-group=nogroup` serving `docker/snmpsim/data/public.snmprec`. snmpsim 0.2.4 refuses to run as root without `--process-user/--process-group`, and it binds the endpoint after dropping privileges, so it listens on unprivileged UDP 1161. `/data` must be readable by `nobody`. |
| `api` | `monplat` app image (`python:2.7.13`) | `gunicorn monplat.api.app:create_app()` on :5000. The app image's entrypoint runs `python setup.py -q develop --no-deps` against the bind-mounted `/app` before `exec "$@"`, so the `mpctl` console script finds its egg-info. |
| `collector` | `monplat` app image | `mpctl collect --interval 30`. |
| `forecast` | `monplat` app image | `mpctl forecast --interval 300`. |
| `pushover-stub` | `monplat` app image | `tests/stubs/pushover_stub.py` on :8025. It records the messages it receives. |
| `grafana` | built from `docker/grafana/Dockerfile` (`node:0.10`) | Uses the Grafana v1.9.1 source tarball and npm tarballs fetched on the host by `make grafana-vendor` (node 0.10's OpenSSL and CA list may fail against today's TLS chains), installs them offline with `npm install --no-optional`, compiles its LESS with less 1.4.2 and `--yui-compress` (the versions and flag of Grafana 1.9.1's own `grunt css` step via grunt-contrib-less ~0.7.0), and serves `src/` statically on :3000 with the generated `config.js` and dashboards. |

Data flow:

1. `mpctl provision` → Zabbix API: templates/items/triggers, hosts, SNMP
   interface, global scripts, media type, action.
2. `collector` → trapper :10051 → Zabbix writes `history`/`history_uint` in
   PostgreSQL. Zabbix also polls `snmpsim` and its own agent.
3. Trigger fires → Zabbix action → `mp_alert.py <to> <subject> <body>` → HTTP
   POST `/api/v1/events` → row in `monplat.events` → Pushover stub → matching
   remediation rule → `script.execute` → row in `monplat.remediations`.
4. `forecast` reads history (SQL) → computes hours-to-threshold → sends a
   trapper value → forecast trigger.
5. Browser → Grafana 1.9.1 → Graphite datasource `http://localhost:20750` (the api's host port) →
   `/metrics/find`, `/render` → SQL over Zabbix history.

## Data model & interfaces

### Zabbix 2.4 tables read (read-only, via psycopg2)

- `hosts(hostid bigint, host varchar, status int)` (status 0 = monitored host, 3 = template)
- `items(itemid bigint, hostid bigint, key_ varchar, name varchar, value_type int, units varchar)`
  (value_type 0 = float → `history`, 3 = unsigned → `history_uint`)
- `history(itemid bigint, clock int, value numeric(16,4), ns int)`
- `history_uint(itemid bigint, clock int, value numeric(20,0), ns int)`

### `monplat` database (`sql/monplat_schema.sql`)

```sql
CREATE TABLE events (
  id            serial PRIMARY KEY,
  eventid       bigint NOT NULL,
  status        varchar(8)  NOT NULL CHECK (status IN ('PROBLEM','OK')),
  host          varchar(128) NOT NULL,
  trigger_id    bigint NOT NULL,
  trigger_name  varchar(255) NOT NULL,
  severity      smallint NOT NULL CHECK (severity BETWEEN 0 AND 5),
  item_value    varchar(255),
  event_time    timestamp with time zone NOT NULL,
  received_at   timestamp with time zone NOT NULL DEFAULT now(),
  notified      boolean NOT NULL DEFAULT false,
  UNIQUE (eventid, status)
);
-- ran = false for refusals (unconfirmed event, host not allowed,
-- rate limit, Zabbix unreachable); only ran rows start a cooldown.
CREATE TABLE remediations (
  id           serial PRIMARY KEY,
  event_id     integer NOT NULL REFERENCES events(id),
  rule         varchar(64) NOT NULL,
  host         varchar(128) NOT NULL,
  script_name  varchar(128) NOT NULL,
  ok           boolean NOT NULL,
  output       text,
  ran          boolean NOT NULL DEFAULT true,
  executed_at  timestamp with time zone NOT NULL DEFAULT now()
);
CREATE INDEX remediations_host_rule_idx ON remediations (host, rule, executed_at);
-- A rule runs at most once per event, however often the event is delivered.
CREATE UNIQUE INDEX remediations_run_once_idx ON remediations (event_id, rule) WHERE ran;
```

### Configuration: `config/monplat.yml`

```yaml
zabbix:
  url: http://zabbix/zabbix          # frontend base; API = <url>/api_jsonrpc.php
  user: Admin
  password: zabbix
  server: zabbix                     # trapper host
  port: 10051
database:
  zabbix_dsn: "host=db dbname=zabbix user=zabbix password=zabbix"
  monplat_dsn: "host=db dbname=monplat user=monplat password=monplat"
api:
  url: http://api:5000
  # Shared secret for POST /api/v1/events, created by "mpctl provision" in
  # the monplat-secrets volume (never committed).
  intake_token_file: /var/lib/monplat/secrets/intake.token
notify:
  pushover_url: http://pushover-stub:8025/1/messages.json
  token: stub-app-token
  user: stub-user-key
remediation:
  rules_file: config/remediation.yml
forecast:
  window_hours: 6
  horizon_hours: 24
  items:
    - {host: mp-collector, key: "mp.fs.pused[/]", threshold: 90, target_key: "mp.forecast.hours_left[/]"}
```

Every key can be overridden by an environment variable `MONPLAT_<SECTION>_<KEY>`,
for example `MONPLAT_ZABBIX_URL`. The file path comes from `MONPLAT_CONFIG`
(default `config/monplat.yml`).

### Template spec: `config/templates/*.yml`

```yaml
template: Template MP Linux
group: MP Templates
applications: [CPU, Memory, Filesystem, Network, Forecast]
items:
  - {key: "mp.cpu.util", name: "CPU utilisation", type: trapper, value_type: float, units: "%", application: CPU}
  - {key: "mp.fs.pused[/]", name: "Root FS used", type: trapper, value_type: float, units: "%", application: Filesystem}
  - {key: "mp.forecast.hours_left[/]", name: "Hours until / reaches threshold", type: trapper, value_type: float, units: h, application: Forecast}
triggers:
  - {name: "High CPU on {HOST.NAME}", expression: "{Template MP Linux:mp.cpu.util.avg(5m)}>90", severity: high}
  - {name: "Root FS full within horizon on {HOST.NAME}", expression: "{Template MP Linux:mp.forecast.hours_left[/].last()}<24", severity: warning}
hosts:
  - {host: mp-collector, groups: [MP Servers], interfaces: []}   # trapper-only host
# SNMP hosts use: interfaces: [{type: snmp, dns: snmpsim, port: 1161}]
```

Item `type` values: `trapper` (Zabbix type 2), `agent` (0), `snmpv2` (4, needs
`snmp_oid` and `snmp_community`). `value_type` values: `float` (0) and
`unsigned` (3). `severity` values: not_classified … disaster (0–5).

A host declared with `interfaces: []` has only trapper items. Zabbix 2.4
rejects `host.create` without interfaces, so such a host is created with a
placeholder agent interface (`127.0.0.1:10050`) that none of its items poll.

Host-template links are added with `host.massadd` (templates), never with a
replacing `host.update templates=[...]`, so templates already linked to a host
(for example Template OS Linux on `Zabbix server`) stay linked.

### CLI: `mpctl`

```
mpctl provision [--templates DIR] [--actions FILE] [--remediation FILE] [--dry-run]  # prints created/updated/unchanged per object; also creates the intake token file
mpctl collect   [--once | --interval SECONDS] [--host NAME]
mpctl forecast  [--once | --interval SECONDS]
mpctl dashboards [--templates DIR] [--out grafana/dashboards]
mpctl send HOST KEY VALUE [--clock EPOCH]         # one-off trapper send (debug / tests)
```

Exit codes: 0 means success. 1 means a runtime error (API or DB unreachable). 2 means the
spec or arguments are invalid.

### Python modules

| Module | Public interface |
|---|---|
| `monplat.config` | `load(path=None) -> dict` |
| `monplat.zabbix.sender` | `encode(data) -> str`, `decode_response(raw) -> dict`, `send(server, port, [(host, key, value, clock)]) -> {'processed': n, 'failed': m, 'total': t}` |
| `monplat.zabbix.api` | `connect(cfg, retries=30) -> ZabbixAPI`, `get_or_create(zapi, obj, filter, params) -> (id, created)` |
| `monplat.templates` | `load_specs(dir) -> [Spec]`, `validate(spec)` (raises `SpecError`), `plan(zapi, spec) -> [Change]`, `apply(zapi, changes)` |
| `monplat.collector` | `sample() -> {key: value}`, `run(cfg, once, interval)` |
| `monplat.history` | `fetch(conn, itemid, start, end, step=None) -> [(value, clock)]`, `bucket(points, step) -> [(avg, clock)]` |
| `monplat.api.app` | `create_app(cfg=None) -> Flask` |
| `monplat.api.graphite` | `metric_path(host, key) -> str`, `find(conn, query) -> [node]`, `parse_time(s, now) -> int`, `render(conn, targets, frm, until, max_points)` |
| `monplat.events` | `parse_alert(subject, body) -> Event`, `store(conn, event) -> id` |
| `monplat.notify` | `priority(severity) -> int`, `push(cfg, event) -> bool` |
| `monplat.intake` | `token_path(cfg)`, `ensure_token(path) -> (token, created)`, `read_token(path)`, `matches(expected, given) -> bool` (constant time) |
| `monplat.remediation` | `rules_path(cfg)`, `load_rules(path)`, `match(rules, event) -> Rule or None` (enabled rules only), `in_cooldown(conn, host, rule, now) -> bool`, `remediate(zapi, conn, event, rules, event_id, now=None) -> dict or None` (`event_id` is the `events` row the `remediations` row refers to) |
| `monplat.forecast` | `fit(points) -> (slope, intercept)`, `hours_to_threshold(points, threshold, now) -> float or None`, `run(cfg, once, interval)` |
| `monplat.dashboards` | `build(spec) -> dict` (Grafana 1.x dashboard, `version: 6`, the schema version of Grafana 1.9's dashboardSrv), `write(specs, outdir)` |

### HTTP API (Flask, port 5000, JSON)

| Method & path | Description |
|---|---|
| `GET /api/v1/health` | `{"zabbix_db": true, "monplat_db": true}`; 503 if either DB is down |
| `GET /api/v1/hosts` | `[{"hostid", "host"}]` of monitored hosts (status 0, host prototypes excluded) |
| `GET /api/v1/hosts/<host>/items` | `[{"itemid", "key", "name", "units", "value_type"}]` |
| `GET /api/v1/items/<itemid>/history?from=&until=&step=` | `[{"clock", "value"}]`. `from` and `until` take the forms `now`, epoch seconds, or `-<n><unit>`. `step` is in seconds. |
| `POST /api/v1/events` | form or JSON `{subject, body}` from the alertscript, with the intake token in `X-Monplat-Token` → 201 `{"id": n, "notified": bool, "remediation": {...} or null}`; 401 without the right token, 503 when no token file exists, 400 for a malformed body, 413 above 64 KiB. This is the only state-changing endpoint; the read-only endpoints need no token. |
| `GET /api/v1/events?host=&status=&limit=` | Newest first |
| `GET /metrics/find/?query=zabbix.*` | Graphite find: `[{"text", "id", "leaf", "expandable", "allowChildren"}]` |
| `GET/POST /render` | Graphite render, `format=json` only: `[{"target", "datapoints": [[value, ts], ...]}]`. Supports `target` (repeated), `from`, `until`, `maxDataPoints`, `*` wildcards and `alias(path, "name")`. |

Time parsing (`monplat.timeparse.parse_time`, shared by the REST and Graphite
endpoints) accepts `now`, epoch seconds and `-<n><unit>` with the Graphite units
`s`, `min`, `h`, `d`, `w`, `mon` (30 days) and `y` (365 days), plus bare `m` as
minutes for the REST API. This matches what Grafana 1.9.1's Graphite datasource
sends: `translateTime` turns `now-5m` into `-5min` and `now-1M` into `-1mon`, and
absolute ranges arrive as epoch seconds.

All responses carry `Access-Control-Allow-Origin: *` so that Grafana 1.x (a
browser-side app) can call the API.

Graphite metric path: `zabbix.<host>.<key>`. In `<host>` and `<key>`, every
character outside `[A-Za-z0-9_-]` becomes `_`, and repeated underscores are
collapsed. For example, `mp.fs.pused[/]` becomes `mp_fs_pused_`. If two keys on
one host sanitise to the same name, `_<itemid>` is appended to each.

### Alertscript contract

The Zabbix media type "MP API" (type 1 = script) calls
`mp_alert.py {ALERT.SENDTO} {ALERT.SUBJECT} {ALERT.MESSAGE}`. The script reads
the intake token from `/var/lib/monplat/secrets/intake.token` (the
`monplat-secrets` volume; `mpctl provision` creates the token on first run, the
zabbix entrypoint gives the directory to the `zabbix` group) and sends it as
`X-Monplat-Token`. The action message template that `mpctl provision` creates
is below. `value=` is the last line: the API takes everything after it as the
value, so an item value containing `"\nstatus=OK"` cannot add or override
fields. Every other line must be one of these keys, given once; `eventid` and
`trigger_id` must be digits, `status` PROBLEM or OK, `severity` 0-5, `time` in
the format shown, and `host` (`{HOST.HOST}`, the technical name) only letters,
digits, space, `.`, `-` and `_`. Bodies above 8192 characters are refused. The action "MP notify API"
has the condition Trigger value = PROBLEM (`conditiontype` 5, `operator` 0,
`value` 1) and `recovery_msg=1`, so each problem yields one PROBLEM message and
one recovery (OK) message, with no duplicate OK rows.

```
eventid={EVENT.ID}
status={TRIGGER.STATUS}
host={HOST.HOST}
trigger_id={TRIGGER.ID}
trigger_name={TRIGGER.NAME}
severity={TRIGGER.NSEVERITY}
time={EVENT.DATE} {EVENT.TIME}
value={ITEM.VALUE}
```

### Remediation rules: `config/remediation.yml`

```yaml
rules:
  - name: clear-spool
    enabled: false                    # opt in per rule
    trigger_match: "^Spool directory too large"
    min_severity: warning
    script: "MP clear spool"          # Zabbix global script, created by provision
    command: "rm -f /var/spool/mp-demo/* && echo cleared"
    expect_output: cleared            # a run only counts as ok with this output
    cooldown_seconds: 600
    allow_hosts: ["Zabbix server"]    # hosts the script may run on
```

`allow_hosts` and/or `allow_groups` (host group names) is required. Only enabled
rules get a global script from `mpctl provision` (custom script, executed on the
agent, `host_access` 3 = write). `make integration` points
`MONPLAT_REMEDIATION_RULES_FILE` at `tests/integration/remediation.yml`, the same
rule with `enabled: true`.

Before a script runs, `remediate()` calls `event.get` (`eventids`, `selectHosts`)
and `trigger.get` (`expandDescription`) and refuses unless the event exists, is a
trigger event with value PROBLEM, is not acknowledged, belongs to the message's
`trigger_id` and to exactly one host equal to the message's `host`, its trigger is
still in PROBLEM with `lastchange` not after the event, and the trigger's own
name and priority match the rule. The host must be in the rule's allow list.
Then the run is claimed: in one transaction behind `pg_advisory_xact_lock` on the
host and rule, the cooldown is checked and a `ran = true` row (`ok = false`, output
"claimed: ...") is inserted and committed, and only then does `script.execute` run;
the row is updated with the outcome afterwards. A concurrent duplicate of the same
event is refused as rate-limited, or, when the cooldown is 0, by the unique index
`remediations_run_once_idx` on `(event_id, rule)` among `ran` rows. Refusals are
stored with `ran = false`.

## Stack & pinned versions

| Component | Version | Release date | Why it was the popular choice in late 2014 |
|---|---|---|---|
| Zabbix server/frontend/agent | 2.4.3 (`2.4.3-1+trusty` debs) | 2014-12-16 (source tarball); trusty debs 2014-12-18 | 2.4 was the current Zabbix branch (2.4.0 came out in September 2014). It was the leading open-source enterprise monitor alongside Nagios. |
| PostgreSQL | 9.3 | 9.3.0 2013-09-09 (9.4 only 2014-12-18) | Mature stable branch. Fully supported Zabbix backend. |
| Ubuntu (Zabbix image base) | 14.04 LTS | 2014-04-17 | Newest LTS. Official Zabbix 2.4 packages were published for trusty. |
| Apache / PHP (frontend) | 2.4.7 / 5.5.9 (trusty archive) | 2013-11 / 2014-02 | Stock trusty LAMP, which is the documented way to run the Zabbix frontend. |
| supervisor | 3.0b2 (trusty archive) | 2013 | The usual way to run several daemons in one Docker container in 2014. |
| Python | 2.7 | 2.7.9 released 2014-12-10 | Default Python for ops tooling. Python 3 had little library support in ops. |
| Flask | 0.10.1 | 2013-06-14 | Most popular Python micro-framework. |
| Werkzeug | 0.9.6 | 2014-06-07 | Flask dependency |
| Jinja2 | 2.7.3 | 2014-06-06 | Flask dependency |
| MarkupSafe | 0.23 | 2014-05-08 | Flask dependency |
| itsdangerous | 0.24 | 2014-03-28 | Flask dependency |
| gunicorn | 19.1.1 | 2014-08-16 | Standard WSGI server |
| requests | 2.5.1 | 2014-12-23 | Standard HTTP client (Pushover, pyzabbix) |
| pyzabbix | 0.7.2 | 2014-10-21 | Most-used Python Zabbix API client |
| psycopg2 | 2.5.4 | 2014-08-30 | The PostgreSQL driver for Python |
| psutil | 2.1.3 | 2014-09-26 | Cross-platform system metrics library |
| PyYAML | 3.11 | 2014-03-27 | YAML config/spec parsing |
| pysnmp | 4.2.5 | 2013-10-02 | Pure-Python SNMP. Required by snmpsim and used by the SNMP sanity test. |
| pyasn1 | 0.1.7 | 2013-05-03 | pysnmp dependency |
| snmpsim | 0.2.4 | 2013-10-04 | SNMP agent simulator (test/dev) |
| pytest | 2.6.4 | 2014-10-24 | Leading Python test runner |
| py | 1.4.26 | 2014-10-24 | pytest dependency |
| mock | 1.0.1 | 2012-11-05 | Standard mocking library on Python 2 |
| Grafana | 1.9.1 | 2014-12-29 (git tag v1.9.1) | Grafana 1.x was the dashboard that replaced graphite-web and Kibana-style UIs in 2014. |
| less (Grafana CSS build only) | 1.4.2 | 2013-07-20 | The version Grafana 1.9.1's own build used (grunt-contrib-less ~0.7.0 depends on less ~1.4.0). Bootstrap 2.3 LESS is compiled with the same compiler. |
| ycssmin | 1.0.1 | 2012-10-24 | less 1.4 dependency behind `--yui-compress` |
| mkdirp | 0.3.5 | 2013-02-22 | less 1.4 dependency |
| mime | 1.2.11 | 2013-08-15 | less 1.4 dependency |
| Node.js (Grafana build image) | 0.10 line | 0.10.x current in 2014 | Runtime for lessc |
| Compose file | Fig 1.0-era service layout, written as a Compose-spec file with no `version` key | Fig 1.0.0 2014-10-16 | Fig was the 2014 multi-container tool. Current `docker compose` no longer reads the legacy v1 (top-level services) format, so the file uses a `services:` key. `zabbix` and `grafana` set `platform: linux/amd64` because the Zabbix 2.4 trusty debs and `node:0.10` are amd64/i386 only. |

Every PyPI and npm pin above was checked with
`tools/check_period.py` (21 checked, 0 problems). The dependency manifests are
`requirements.txt`, `requirements-dev.txt` and `docker/grafana/package.json`.
less 1.4.2 also declares `request >=2.12.0` (an open range that would resolve to
a post-2014 release), so the Grafana image installs with `npm install
--no-optional` and pins less's other dependencies explicitly; `request` (used
only for `@import` over HTTP) is never installed.
Zabbix, Grafana and the OS packages have no registry. Their dates come from the
Zabbix package pool (`repo.zabbix.com/zabbix/2.4/ubuntu/pool/main/z/zabbix/`,
dated 2014-Dec-18), the `cdn.zabbix.com` tarball Last-Modified header
(2014-12-16) and the Grafana git tag.

## Docker images

| Image tag | Hub check | Pullable (manifest v2) | Notes |
|---|---|---|---|
| `python:2.7.13` | 200 | yes | **Substitute.** The period tags `python:2.7.8` and `python:2.7.9` still exist on Docker Hub but have only schema-v1 manifests, which current Docker cannot pull. `2.7.13` is the oldest pullable 2.7 tag. The language level is still Python 2.7. Its base is Debian 8 (jessie), whose `deb.debian.org` and `security.debian.org` indexes now return 404, so **the Dockerfile runs no `apt-get`**: gcc, git and `libpq-dev` already come from the `buildpack-deps:jessie` layers, which is enough to build psycopg2 2.5.4 and psutil 2.1.3. If an apt package is ever needed, rewrite `sources.list` to `archive.debian.org/debian jessie` and use `apt-get -o Acquire::Check-Valid-Until=false update`. |
| `python:2.7` | 200 | yes | Fallback if 2.7.13 cannot build psutil/psycopg2 (resolves to 2.7.18 on Debian 10 buster). Same rule: no apt, because buster is also gone from `deb.debian.org`; use `archive.debian.org` if needed. |
| `postgres:9.3` | 200 | yes | **Substitute.** `postgres:9.3.5` exists but is schema-v1 only. `9.3` resolves to 9.3.25, the same major version. |
| `ubuntu:14.04` | 200 | yes | Zabbix image base. The trusty apt archive and `repo.zabbix.com/zabbix/2.4/ubuntu` both still answer. |
| `node:0.10` | 200 | yes | Grafana CSS build. `node:0.10.33` is schema-v1 only. `0.10` resolves to 0.10.48 (jessie base, so no apt either). |

Checked with `curl https://hub.docker.com/v2/repositories/library/<name>/tags/<tag>`
and `docker manifest inspect`.

## Simulated/mocked integrations

| Real system | Replacement | Where |
|---|---|---|
| SNMP network device (switch/router) | **snmpsim 0.2.4** (`snmpsimd.py`) serving `docker/snmpsim/data/public.snmprec` (sysDescr, sysUpTime, IF-MIB counters for 2 interfaces) | `snmpsim` compose service |
| Pushover mobile push API | **pushover-stub**: a small Flask app that accepts `POST /1/messages.json`, answers `{"status":1,"request":"<uuid>"}`, and keeps the received messages for `GET /_received` | `tests/stubs/pushover_stub.py`, `pushover-stub` service |
| Production Linux servers | The `collector` and `zabbix` containers monitor themselves. A demo spool directory (`/var/spool/mp-demo`, a named volume shared with the test runner) in the `zabbix` container is the remediation target. | compose |
| Zabbix API in unit tests | `mock.MagicMock` stand-in for `pyzabbix.ZabbixAPI` | `tests/unit/` |
| Zabbix trapper in unit tests | An in-process fake trapper socket server (a thread) | `tests/unit/test_sender.py` |

Zabbix itself is **not** mocked in integration tests. They run against the real
Zabbix 2.4.3 server.

## Existing code inventory

`git ls-files` at HEAD lists exactly one file.

| File | Decision | Reason |
|---|---|---|
| `README.md` | refactor | It says "not implemented" and lists the sketched scope. The last task regenerates it from `tools/readme_template.md`, with an honest status and no fabricated claims. |

`docs/SPEC.md` and `docs/IMPLEMENTATION_PLAN.md` (this document and the plan)
are new and are kept. No existing code can be reused, so every module in the
plan is new.

## Test strategy

- **Runner:** `make test` runs `make unit` and then `make integration`. Both
  run pytest 2.6.4 inside the `python:2.7.13`-based app image via
  `docker compose run --rm`; `make unit` adds `--no-deps` so no other service
  starts.
- **Unit tests** (`tests/unit/`, no network): these cover
  - config loading and env overrides,
  - sender framing and response parsing against a fake socket server,
  - template spec validation and change planning against a mocked API,
  - collector sampling with psutil mocked,
  - history bucketing,
  - Graphite path sanitising, collisions, wildcard matching, time parsing
    (including `-5min`, `-15min`, `-7d`, `-1mon` and epoch) and render shaping,
  - alert parsing, including duplicate/unknown keys, field validation and
    injected lines inside `value`,
  - intake token creation and constant-time checking, and 401/503/413 on
    `POST /api/v1/events`,
  - bounded trapper reply reads (size cap and one overall deadline),
  - credentials absent from errors, CLI output, logs and HTTP responses,
  - Dockerfiles: no apt signature bypass, no plain-HTTP fetches, vendored
    files checked against committed SHA-256 sums; compose ports on 127.0.0.1,
  - Pushover priority mapping and the request body (requests mocked),
  - remediation rule matching, opt-in, allow lists, confirmation of the event
    with Zabbix, refusal auditing and cooldown,
  - least-squares fit and hours-to-threshold edge cases (flat or decreasing
    series, too few points),
  - dashboard JSON structure,
  - CLI argument handling and exit codes.
- **Integration tests** (`tests/integration/`, marked `integration`). These run
  against the compose stack. `make integration` starts it and waits until
  `apiinfo.version` answers, then runs `mpctl provision` once, and only then
  starts `collector` and `forecast`, so nothing else provisions concurrently.
  They check:
  - API login and `apiinfo.version == "2.4.3"`,
  - that provisioning is idempotent (a run after the setup provision: all
    unchanged, template and hosts present),
  - sender → `history.get` round trip,
  - that Zabbix polls the SNMP item from snmpsim,
  - history SQL reads of values sent with known clocks,
  - the REST and Graphite endpoints over HTTP,
  - that Grafana serves `index.html`, the compiled CSS and the generated
    dashboard,
  - the full alert chain: a breaching trapper value leads to a Zabbix action,
    `mp_alert.py` (with the intake token), a stored event, a stub push and a
    remediation row; POSTs without the token are refused and store nothing;
    a forged, authenticated event for a real host runs no script and is
    audited as refused,
  - forecast over a synthetic rising series leads to the expected
    hours-left value.
- **Isolation:** a session fixture provisions one dedicated host per test
  module, each linked to Template MP Linux: `mp-test-collect` (collector),
  `mp-test-alert` (alert chain) and `mp-test-forecast` (forecast), so samples
  from one test never land in another's window. `make integration` runs
  `make down` (which removes volumes) first, so each run starts clean.
- Trapper sends in tests go through a conftest helper that retries
  `sender.send` until `processed == total` (timeout 60 s), because Zabbix only
  accepts values for items once its configuration cache has picked them up.
- Timing-dependent assertions (Zabbix polling, actions) poll with a timeout of
  up to 120 s instead of sleeping for a fixed time.

## Known limitations that will remain

- The Docker images are the nearest pullable tags (Python 2.7.13, PostgreSQL
  9.3.25, Node 0.10.48), not the exact late-2014 point releases. The
  Zabbix/Grafana/library versions are period-exact.
- The Graphite bridge implements only `find` and `render` (JSON), with `*`
  wildcards and `alias()`. It has no other Graphite functions and no PNG
  rendering.
- Grafana dashboards are read-only files. There is no dashboard storage backend.
- The forecast is a plain linear fit over one window. It ignores seasonality.
- Remediation runs only pre-registered Zabbix global scripts. Agents must allow
  remote commands (`EnableRemoteCommands=1`), which is a security trade-off that
  is acceptable only in this lab.
- Pushover is only ever a local stub. No real push is sent.
- No TLS on the monplat API. Only event intake is authenticated (shared
  intake token); the read-only endpoints are open so the browser-side Grafana
  can call them. Published ports listen on 127.0.0.1 only.
- The Zabbix 2.4.3 `.deb`s and the Grafana source tarball have no signed
  index to check against (the Zabbix repository index only lists the newest
  2.4.x), so they are pinned by SHA-256 sums recorded from an HTTPS download
  (`docker/zabbix/SHA256SUMS`, `docker/grafana/SHA256SUMS`; the npm tarballs
  also match the registry's SHA-1). The trusty archive is fetched by apt over
  HTTP, with apt verifying the archive's signed Release files.
- WMI/Windows monitoring is not implemented.
- The stack is orchestrated with a modern `docker compose` file, because
  current Docker no longer reads Fig 1.0 / Compose v1 files.
- The monitored "servers" are the stack's own containers; no real servers are
  monitored.
