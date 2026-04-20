SHELL := /bin/bash

export NO_MKDOCS_2_WARNING := true

.PHONY: demo-sphinx demo-mkdocs docs-serve docs-build docs-tests install-docs install-tests

install-docs:
	python3 -m pip install -e ".[audit,cli]" -q -r docs/requirements.txt -r demo-docs/sphinx/requirements.txt -r demo-docs/mkdocs/requirements.txt

install-tests:
	python3 -m pip install -e ".[audit,cli]" -q -r tests/requirements.txt

demo-sphinx:
	cd demo-docs/sphinx && make html SPHINXBUILD="python3 -m sphinx"

demo-mkdocs:
	cd demo-docs/mkdocs && python3 -m mkdocs build --strict

docs-build: install-docs demo-sphinx demo-mkdocs
	rm -rf docs/assets
	cp -r assets docs/assets
	python3 -m mkdocs build --strict
	cp -r demo-docs/sphinx/build/html/. site/demo-sphinx/
	cp -r demo-docs/mkdocs/site/. site/demo-mkdocs/

docs-serve: docs-build
	python3 -m http.server 8000 --directory site --bind 127.0.0.1

docs-tests: install-tests
	find tests/docs -name "test_*.py" -delete
	python3 -m phmdoctest README.md --outfile tests/integration/test_readme.py
	for md in $$(find docs -name "*.md" | sort); do \
		name="$${md#docs/}"; name="$${name%.md}"; name="$${name//\//_}"; name="$${name//-/_}"; \
		python3 -m phmdoctest "$$md" --outfile "tests/docs/test_$${name}.py"; \
	done
