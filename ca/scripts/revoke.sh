#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

CERT="${1:?Pemakaian: revoke.sh path/ke/cert.crt [alasan]}"
REASON="${2:-unspecified}"

openssl ca -config "$OPENSSL_CONF" -name CA_default \
    -revoke "$CERT" -crl_reason "$REASON" -batch

"$(dirname "${BASH_SOURCE[0]}")/gen-crl.sh"
