#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

init_ca_dirs "$ROOT_DIR"

if [ -f "$ROOT_DIR/certs/root.crt" ]; then
    echo "Root CA sudah ada: $ROOT_DIR/certs/root.crt"
    exit 0
fi

echo "Membuat root CA (offline) di $ROOT_DIR ..."
openssl genrsa -out "$ROOT_DIR/private/root.key" 4096
chmod 600 "$ROOT_DIR/private/root.key"

openssl req -new -x509 -sha256 -days 3650 \
    -key "$ROOT_DIR/private/root.key" \
    -out "$ROOT_DIR/certs/root.crt" \
    -subj "/CN=${ROOT_CN:-$ORG Root CA}/O=$ORG/C=ID" \
    -config "$OPENSSL_CONF" \
    -extensions v3_root_ca

echo "Root CA dibuat:"
openssl x509 -in "$ROOT_DIR/certs/root.crt" -noout -subject -fingerprint -sha256
