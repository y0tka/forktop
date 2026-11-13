PY ?= python3
.PHONY: help setup export check lint status rebase continue finalize hooks test doctor

help:
	@printf '%s\n' 'setup export check lint status rebase TAG=... continue finalize hooks test doctor'

setup:    ; $(PY) scripts/patchset.py setup
export:   ; $(PY) scripts/patchset.py export
check:    ; $(PY) scripts/patchset.py check
lint:     ; $(PY) scripts/patchset.py lint --check
status:   ; $(PY) scripts/patchset.py status
rebase:
	@test -n "$(TAG)" || (echo 'usage: make rebase TAG=latest|v1.2.13' >&2; exit 2)
	$(PY) scripts/patchset.py rebase "$(TAG)"
continue: ; $(PY) scripts/patchset.py continue
finalize: ; $(PY) scripts/patchset.py finalize
hooks:    ; git config core.hooksPath .githooks
test:     ; $(PY) -m unittest discover -s scripts/tests -t scripts -v
doctor:   ; $(PY) scripts/patchset.py doctor
