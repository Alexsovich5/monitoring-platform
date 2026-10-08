build:
	docker compose build app

unit:
	docker compose run --rm --no-deps app py.test -q tests/unit

test: unit

down:
	docker compose down -v

shell:
	docker compose run --rm app bash

.PHONY: build unit test down shell
