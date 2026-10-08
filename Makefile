STACK = db zabbix snmpsim api pushover-stub grafana

GRAFANA_VENDOR = docker/grafana/vendor
ZABBIX_VENDOR = docker/zabbix/vendor
ZABBIX_POOL = https://repo.zabbix.com/zabbix/2.4/ubuntu/pool/main/z/zabbix
ZABBIX_DEBS = zabbix-server-pgsql_2.4.3-1+trusty_amd64.deb \
	zabbix-agent_2.4.3-1+trusty_amd64.deb \
	zabbix-frontend-php_2.4.3-1+trusty_all.deb
CURL = curl -fsSL --retry 3 --retry-delay 5
NPM_REGISTRY = https://registry.npmjs.org

build: grafana-vendor zabbix-vendor
	docker compose build app zabbix grafana

# Downloads the Zabbix 2.4.3 packages on the host over HTTPS; the image
# build checks them against docker/zabbix/SHA256SUMS.
zabbix-vendor: $(addprefix $(ZABBIX_VENDOR)/,$(ZABBIX_DEBS))

$(ZABBIX_VENDOR)/%.deb:
	mkdir -p $(ZABBIX_VENDOR)
	$(CURL) -o $@.part '$(ZABBIX_POOL)/$*.deb'
	mv $@.part $@

# Downloads Grafana's source and the LESS toolchain on the host, so the
# node:0.10 image never has to make a TLS connection itself; the image
# build checks them against docker/grafana/SHA256SUMS.
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

# The integration run enables the clear-spool remediation rule.
integration: export MONPLAT_REMEDIATION_RULES_FILE = tests/integration/remediation.yml
integration: down
	docker compose up -d $(STACK)
	docker compose run --rm --no-deps app scripts/wait_for_stack.sh
	docker compose run --rm app mpctl provision
	docker compose up -d collector forecast
	docker compose run --rm --no-deps --use-aliases app py.test -q -m integration tests/integration

test: unit integration

down:
	docker compose down -v

shell:
	docker compose run --rm app bash

.PHONY: build grafana-vendor zabbix-vendor unit integration test down shell
