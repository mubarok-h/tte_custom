#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SIGNER_NAME="${SIGNER_NAME:-Rektor STIT Buntet Pesantren}"
SIGNER_NIP="${SIGNER_NIP:-1987654321}"
SIGNER_EMAIL="${SIGNER_EMAIL:-rektor@stit-buntetpesantren.ac.id}"

"$SCRIPT_DIR/init-root.sh"
"$SCRIPT_DIR/init-intermediate.sh"
"$SCRIPT_DIR/issue-leaf.sh" "$SIGNER_NAME" "$SIGNER_NIP" "$SIGNER_EMAIL"
"$SCRIPT_DIR/gen-crl.sh"

mkdir -p "$PKI_OUT/crl" "$PKI_OUT/tsa"
cp "$ROOT_DIR/certs/root.crt" "$PKI_OUT/root.crt"
cp "$INT_DIR/certs/intermediate.crt" "$PKI_OUT/intermediate.crt"
if [ -d "$CA_DIR/tsa-roots" ]; then
    cp "$CA_DIR/tsa-roots/"*.crt "$PKI_OUT/tsa/" 2>/dev/null || true
fi

SLUG="$(slugify "$SIGNER_NAME")"
cp "$LEAF_DIR/$SLUG.p12" "$PKI_OUT/rektor.p12"
chmod 644 "$PKI_OUT/root.crt" "$PKI_OUT/intermediate.crt"
chmod 600 "$PKI_OUT/rektor.p12"

echo
echo "Bootstrap selesai."
echo "  root         : $PKI_OUT/root.crt"
echo "  intermediate : $PKI_OUT/intermediate.crt"
echo "  p12          : $PKI_OUT/rektor.p12 (pass: ${PKCS12_PASS:-changeit})"
echo "  crl          : $PKI_OUT/crl/stit.crl"
