STACK = db zabbix

build:
	docker compose build app zabbix

unit:
	docker compose run --rm --no-deps app py.test -q tests/unit

integration: down
	docker compose up -d $(STACK)
	docker compose run --rm --no-deps app scripts/wait_for_stack.sh
	docker compose run --rm app mpctl provision
	docker compose run --rm --no-deps --use-aliases app py.test -q -m integration tests/integration

test: unit integration

down:
	docker compose down -v

shell:
	docker compose run --rm app bash

.PHONY: build unit integration test down shell
