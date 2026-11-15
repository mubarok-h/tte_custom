#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

init_ca_dirs "$ROOT_DIR"
init_ca_dirs "$INT_DIR"

if [ -f "$INT_DIR/certs/intermediate.crt" ]; then
    echo "Intermediate CA sudah ada: $INT_DIR/certs/intermediate.crt"
    exit 0
fi

echo "Membuat intermediate CA di $INT_DIR ..."
openssl genrsa -out "$INT_DIR/private/intermediate.key" 3072
chmod 600 "$INT_DIR/private/intermediate.key"

openssl req -new -sha256 \
    -key "$INT_DIR/private/intermediate.key" \
    -out "$INT_DIR/intermediate.csr" \
    -subj "/CN=${INT_CN:-$ORG Signing CA}/O=$ORG/C=ID" \
    -config "$OPENSSL_CONF"

openssl ca -config "$OPENSSL_CONF" -name CA_root \
    -extensions v3_intermediate_ca \
    -days 1825 -notext -batch \
    -in "$INT_DIR/intermediate.csr" \
    -out "$INT_DIR/certs/intermediate.crt"

echo "Intermediate CA dibuat:"
openssl x509 -in "$INT_DIR/certs/intermediate.crt" -noout -subject -fingerprint -sha256
