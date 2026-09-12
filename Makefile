.PHONY: install install-llm setup test run
install:        ; pip install -r requirements.txt
install-llm:    ; pip install -r requirements-llm.txt
setup:          ; python scripts/setup_model.py
test:           ; pytest
run:            ; python app.py
