-- Event log and remediation history kept by the monplat API.
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
