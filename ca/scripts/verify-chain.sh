#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CERT="${1:?Pemakaian: verify-chain.sh path/ke/cert.crt}"
openssl verify -CAfile "$ROOT_DIR/certs/root.crt" \
    -untrusted "$INT_DIR/certs/intermediate.crt" "$CERT"
