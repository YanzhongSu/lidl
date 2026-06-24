.PHONY: smoke

PYTHON ?= python3

smoke:
	$(PYTHON) -m py_compile scripts/lidl_receipts.py
	$(PYTHON) -m unittest discover -s tests
	$(PYTHON) scripts/lidl_receipts.py --help >/dev/null
	@if [ -f data/receipts_summaries.json ]; then $(PYTHON) scripts/lidl_receipts.py status >/dev/null; fi
	@if [ -f data/receipts_detail.json ]; then $(PYTHON) scripts/lidl_receipts.py query --days 1 >/dev/null; fi
