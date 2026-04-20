SHELL := /bin/bash

.PHONY: demo-sphinx demo-mkdocs docs-serve docs-build docs-tests

demo-sphinx:
	cd demo-docs/sphinx && make html

demo-mkdocs:
	cd demo-docs/mkdocs && python3 -m mkdocs build --strict

docs-build: demo-sphinx demo-mkdocs
	rm -rf docs/assets
	cp -r assets docs/assets
	python3 -m mkdocs build --strict
	cp -r demo-docs/sphinx/build/html/. site/demo-sphinx/
	cp -r demo-docs/mkdocs/site/. site/demo-mkdocs/

docs-serve: demo-sphinx demo-mkdocs
	rm -rf docs/assets
	cp -r assets docs/assets
	python3 -m mkdocs serve

docs-tests:
	python3 -m phmdoctest README.md --outfile tests/integration/test_readme.py
	for md in $$(find docs -name "*.md" | sort); do \
		name="$${md#docs/}"; name="$${name%.md}"; name="$${name//\//_}"; name="$${name//-/_}"; \
		python3 -m phmdoctest "$$md" --outfile "tests/docs/test_$${name}.py"; \
	done
