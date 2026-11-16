.PHONY: all check check-docs check-doc-versions check-evidence check-i18n check-core test-core build-webui yocto-validate

all: check

check: check-docs check-doc-versions check-evidence check-i18n yocto-validate

check-docs:
	python3 scripts/check_traceability.py

check-doc-versions:
	python3 scripts/check_doc_versions.py

check-evidence:
	python3 scripts/check_evidence.py --test
	python3 scripts/check_evidence.py

check-i18n:
	node scripts/check_i18n.mjs

check-core:
	cargo check --locked --manifest-path core/Cargo.toml --all-targets
	cargo clippy --locked --manifest-path core/Cargo.toml --all-targets -- -D warnings

test-core:
	cargo test --locked --manifest-path core/Cargo.toml

build-webui:
	npm --prefix webui run build

yocto-validate:
	python3 scripts/check_yocto_layer.py --test
	python3 scripts/check_yocto_layer.py
	python3 scripts/test_tone_check.py
