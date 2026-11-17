PY ?= .venv/bin/python

.PHONY: setup demo demo-llm test results clean

setup:            ## create .venv with Python 3.12 (uv) and install in editable mode with dev extras
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e '.[dev]'

demo:             ## run all demo scenarios with the offline rule-based diagnosis
	$(PY) -m automation_doctor demo --provider rules

demo-llm:         ## same, with a local Ollama model (AD_OLLAMA_MODEL, default qwen2.5-coder:7b)
	@echo "NOTE: LLM diagnosis is slow: expect ~80 s per failing scenario on an M1 / 16 GB (7 diagnoses, ~9 min total)."
	@echo "      Verdicts do not depend on the model. Ctrl-C to stop; 'make demo' is the fast offline version."
	$(PY) -m automation_doctor demo --provider ollama --out demo-output-llm

test:
	$(PY) -m pytest -q

results:          ## regenerate the committed measured results
	$(PY) -m automation_doctor demo --provider rules --results-file results/demo-rules.md

clean:
	rm -rf demo-output demo-output-llm .automation-doctor .pytest_cache
