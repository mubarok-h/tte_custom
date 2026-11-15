#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

NAME="${1:?Pemakaian: issue-leaf.sh \"Nama Pejabat\" NIP email [outdir]}"
NIP="${2:?NIP wajib diisi}"
EMAIL="${3:-}"
OUT="${4:-$LEAF_DIR}"

mkdir -p "$OUT"
SLUG="$(slugify "$NAME")"
KEY="$OUT/$SLUG.key"
CSR="$OUT/$SLUG.csr"
CRT="$OUT/$SLUG.crt"
P12="$OUT/$SLUG.p12"

SUBJ="/CN=$NAME/OU=$ORG/O=$ORG/C=ID"
[ -n "$NIP" ] && SUBJ="$SUBJ/serialNumber=$NIP"
[ -n "$EMAIL" ] && SUBJ="$SUBJ/emailAddress=$EMAIL"

echo "Menerbitkan leaf untuk $NAME ($NIP) ..."
openssl genrsa -out "$KEY" 2048
chmod 600 "$KEY"

openssl req -new -sha256 -key "$KEY" -out "$CSR" \
    -subj "$SUBJ" -config "$OPENSSL_CONF"

openssl ca -config "$OPENSSL_CONF" -name CA_default \
    -extensions v3_signing \
    -days "${LEAF_DAYS:-730}" -notext -batch \
    -in "$CSR" -out "$CRT"

openssl pkcs12 -export -out "$P12" \
    -inkey "$KEY" -in "$CRT" \
    -certfile "$INT_DIR/certs/intermediate.crt" \
    -passout "pass:${PKCS12_PASS:-changeit}"
chmod 600 "$P12"

echo "Selesai:"
echo "  cert : $CRT"
echo "  p12  : $P12"
openssl x509 -in "$CRT" -noout -subject -issuer -dates
