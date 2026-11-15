#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

mkdir -p "$INT_DIR/crl" "$ROOT_DIR/crl" "$PKI_OUT/crl"

openssl ca -config "$OPENSSL_CONF" -name CA_default \
    -gencrl -out "$INT_DIR/crl/stit.crl"

openssl ca -config "$OPENSSL_CONF" -name CA_root \
    -gencrl -out "$ROOT_DIR/crl/root.crl"

cp "$INT_DIR/crl/stit.crl" "$PKI_OUT/crl/stit.crl"
cp "$ROOT_DIR/crl/root.crl" "$PKI_OUT/crl/root.crl"
chmod 644 "$PKI_OUT/crl/stit.crl" "$PKI_OUT/crl/root.crl"

echo "CRL dibuat:"
echo "  $PKI_OUT/crl/stit.crl"
echo "  $PKI_OUT/crl/root.crl"
openssl crl -in "$PKI_OUT/crl/stit.crl" -noout -lastupdate -nextupdate
