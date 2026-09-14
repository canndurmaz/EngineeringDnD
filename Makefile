.PHONY: install install-llm setup test lint run clean
install:        ; pip install -r requirements.txt
install-llm:    ; pip install -r requirements-llm.txt
setup:          ; python scripts/setup_model.py
test:           ; pytest
run:            ; python app.py
clean:          ; rm -rf .pytest_cache **/__pycache__
