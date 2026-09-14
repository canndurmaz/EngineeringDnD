.PHONY: setup install install-llm model test run clean

PY = ./venv/bin/python

setup:          ; ./setup.sh
install:        ; $(PY) -m pip install -r requirements.txt
install-llm:    ; $(PY) -m pip install -r requirements-llm.txt
model:          ; $(PY) scripts/setup_model.py
test:           ; $(PY) -m pytest
run:            ; $(PY) app.py
clean:          ; rm -rf .pytest_cache **/__pycache__
