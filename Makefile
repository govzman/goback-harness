PYTHON ?= python3
PAIR ?= goback+gpt-6.1-terra-low
REPEATS ?= 3

.PHONY: up down test-ci test-ui test-infra baf-smoke baf compare-undo bench-fs bench-sandbox
up:
	docker compose up --build
down:
	docker compose down -v
test-ci: test-ui test-infra baf-smoke
test-ui:
	$(PYTHON) tests/ui/test_client.py
test-infra:
	$(PYTHON) tests/infra/test_runtime.py
baf-smoke:
	$(PYTHON) baf/run.py --smoke --pair goback+fixture
baf:
	$(PYTHON) baf/run.py --pair "$(PAIR)" --repeats $(REPEATS) --out results/baf.jsonl
compare-undo:
	$(PYTHON) tests/infra/compare_undo.py
bench-fs:
	$(PYTHON) tests/infra/bench_env.py --only fs --out results/bench-fs.json
bench-sandbox:
	$(PYTHON) tests/infra/bench_env.py --only sandbox --out results/bench-sandbox.json
