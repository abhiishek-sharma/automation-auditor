PY ?= .venv/bin/python

.PHONY: setup demo demo-llm test results clean

setup:            ## create .venv with Python 3.12 (uv) and install in editable mode with dev extras
	uv venv --python 3.12 .venv
	uv pip install --python $(PY) -e '.[dev]'

demo:             ## run all demo scenarios with the offline rule-based diagnosis
	$(PY) -m automation_doctor demo --provider rules

demo-llm:         ## same, with a local Ollama model (AD_OLLAMA_MODEL, default qwen3.5:9b)
	$(PY) -m automation_doctor demo --provider ollama --out demo-output-llm

test:
	$(PY) -m pytest -q

results:          ## regenerate the committed measured results
	$(PY) -m automation_doctor demo --provider rules --results-file results/demo-rules.md

clean:
	rm -rf demo-output demo-output-llm .automation-doctor .pytest_cache
