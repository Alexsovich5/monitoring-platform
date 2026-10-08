STACK = db zabbix snmpsim api grafana

GRAFANA_VENDOR = docker/grafana/vendor
CURL = curl -fsSL --retry 3 --retry-delay 5
NPM_REGISTRY = https://registry.npmjs.org

build: grafana-vendor
	docker compose build app zabbix grafana

# Downloads Grafana's source and the LESS toolchain on the host, so the
# node:0.10 image never has to make a TLS connection itself.
grafana-vendor: $(GRAFANA_VENDOR)/v1.9.1.tar.gz \
	$(GRAFANA_VENDOR)/less-1.4.2.tgz $(GRAFANA_VENDOR)/ycssmin-1.0.1.tgz \
	$(GRAFANA_VENDOR)/mkdirp-0.3.5.tgz $(GRAFANA_VENDOR)/mime-1.2.11.tgz

$(GRAFANA_VENDOR)/v1.9.1.tar.gz:
	mkdir -p $(GRAFANA_VENDOR)
	$(CURL) -o $@.part https://github.com/grafana/grafana/archive/v1.9.1.tar.gz
	mv $@.part $@

$(GRAFANA_VENDOR)/%.tgz:
	mkdir -p $(GRAFANA_VENDOR)
	$(CURL) -o $@.part $(NPM_REGISTRY)/$(shell echo $* | sed 's/-[0-9][0-9.]*$$//')/-/$*.tgz
	mv $@.part $@

unit:
	docker compose run --rm --no-deps app py.test -q tests/unit

integration: down
	docker compose up -d $(STACK)
	docker compose run --rm --no-deps app scripts/wait_for_stack.sh
	docker compose run --rm app mpctl provision
	docker compose up -d collector
	docker compose run --rm --no-deps --use-aliases app py.test -q -m integration tests/integration

test: unit integration

down:
	docker compose down -v

shell:
	docker compose run --rm app bash

.PHONY: build grafana-vendor unit integration test down shell
