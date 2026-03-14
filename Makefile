.PHONY: clean prepare bump dist release create-release install check_dist upload

PYCACHE := $(shell find . -name '__pycache__')
EGGS := $(wildcard *.egg-info src/*.egg-info)
CURRENT_VERSION := $(shell awk '/current_version =/ {print substr($$3, 2, length($$3)-2)}' pyproject.toml)

clean:
	@echo "=> Cleaning"
	@rm -fr build dist $(EGGS) $(PYCACHE)

prepare: clean
	git add .
	git status
	git commit -m "cleanup before release"

# Version commands

bump:
	@echo Current version: $(CURRENT_VERSION)
ifdef part
	bump-my-version bump $(part) --allow-dirty && grep version pyproject.toml
else ifdef version
	bump-my-version bump --allow-dirty --new-version $(version) && grep version pyproject.toml
else
	@echo "Provide part=major|minor|patch|release|build and optionally version=x.y.z..."
	exit 1
endif

# Dist commands

dist:
	@rm -f dist/*
	@python -m build -n

release:
	git add .
	git status
	git diff-index --quiet HEAD || git commit -m "Latest release: $(CURRENT_VERSION)"
	git tag -a v$(CURRENT_VERSION) -m "Latest release: $(CURRENT_VERSION)"

create-release:
	@github-release release -u bernhard-42 -r threejs-materials -t v$(CURRENT_VERSION) -n threejs_materials-$(CURRENT_VERSION)
	@sleep 2
	@github-release upload  -u bernhard-42 -r threejs-materials -t v$(CURRENT_VERSION) -n threejs_materials-$(CURRENT_VERSION).tar.gz -f dist/threejs_materials-$(CURRENT_VERSION).tar.gz
	@github-release upload  -u bernhard-42 -r threejs-materials -t v$(CURRENT_VERSION) -n threejs_materials-$(CURRENT_VERSION)-py3-none-any.whl -f dist/threejs_materials-$(CURRENT_VERSION)-py3-none-any.whl

install: dist
	@echo "=> Installing threejs-materials"
	@pip install --upgrade .

check_dist:
	@twine check dist/*

upload:
	@twine upload dist/*
