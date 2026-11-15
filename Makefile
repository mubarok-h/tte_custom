.PHONY: help venv bootstrap dev test docker-up docker-down issue revoke crl verify audit archive

help:
	@echo "Target:"
	@echo "  venv        Buat virtualenv dan install dependensi"
	@echo "  bootstrap   Buat CA dev + leaf Rektor + CRL"
	@echo "  dev         Jalankan server lokal (reload)"
	@echo "  test        Jalankan test"
	@echo "  docker-up   Jalankan docker compose"
	@echo "  docker-down Hentikan docker compose"
	@echo "  issue       Terbitkan leaf: make issue NAME='...' NIP='...' EMAIL='...'"
	@echo "  revoke      Cabut leaf: make revoke CERT=path/ke/cert.crt"
	@echo "  crl         Perbarui CRL"
	@echo "  verify      Verifikasi chain: make verify CERT=path/ke/cert.crt"
	@echo "  audit       Periksa hash-chain audit log"
	@echo "  archive     Arsipkan dokumen yang melewati masa retensi"

venv:
	python3 -m venv --without-pip .venv
	curl -fsSL https://bootstrap.pypa.io/get-pip.py | .venv/bin/python -q
	.venv/bin/pip install -q -r requirements-dev.txt

bootstrap:
	bash ca/scripts/bootstrap-dev.sh

dev:
	.venv/bin/uvicorn tte_core.app:app --reload --host 0.0.0.0 --port 8000

test:
	.venv/bin/python -m pytest -q

docker-up:
	docker compose up -d --build

docker-down:
	docker compose down

issue:
	bash ca/scripts/issue-leaf.sh "$(NAME)" "$(NIP)" "$(EMAIL)"

revoke:
	bash ca/scripts/revoke.sh "$(CERT)"

crl:
	bash ca/scripts/gen-crl.sh

verify:
	bash ca/scripts/verify-chain.sh "$(CERT)"

audit:
	.venv/bin/python -m tte_core.maintenance audit

archive:
	.venv/bin/python -m tte_core.maintenance archive
