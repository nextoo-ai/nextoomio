.PHONY: all check check-docs check-i18n check-core test-core build-webui yocto-validate

all: check

check: check-docs check-i18n

check-docs:
	python3 scripts/check_traceability.py

check-i18n:
	node scripts/check_i18n.mjs

check-core:
	cargo check --manifest-path core/Cargo.toml --all-targets
	cargo clippy --manifest-path core/Cargo.toml --all-targets -- -D warnings

test-core:
	cargo test --manifest-path core/Cargo.toml

build-webui:
	npm --prefix webui run build

yocto-validate:
	python3 scripts/check_yocto_layer.py
