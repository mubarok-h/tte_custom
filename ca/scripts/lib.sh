#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CA_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
REPO_DIR="$(cd "$CA_DIR/.." && pwd)"

export CA_DIR
export REPO_DIR
export OPENSSL_CONF="${OPENSSL_CONF:-$CA_DIR/openssl.cnf}"
export DEV_DIR="${DEV_DIR:-$CA_DIR/dev}"
export ROOT_DIR="${ROOT_DIR:-$DEV_DIR/root}"
export INT_DIR="${INT_DIR:-$DEV_DIR/intermediate}"
export LEAF_DIR="${LEAF_DIR:-$DEV_DIR/leaves}"
export PKI_OUT="${PKI_OUT:-$REPO_DIR/data/pki}"

export ORG="${ORG:-STIT Buntet Pesantren}"
export CRL_URL="${CRL_URL:-https://verif.stit-buntetpesantren.ac.id/crl/stit.crl}"
export ROOT_CRL_URL="${ROOT_CRL_URL:-https://verif.stit-buntetpesantren.ac.id/crl/root.crl}"
export CA_ISSUERS_URL="${CA_ISSUERS_URL:-https://verif.stit-buntetpesantren.ac.id/ca/intermediate.crt}"

init_ca_dirs() {
    local dir="$1"
    mkdir -p "$dir/certs" "$dir/private" "$dir/newcerts" "$dir/crl"
    chmod 700 "$dir/private"
    touch "$dir/index.txt"
    [ -f "$dir/serial" ] || echo 1000 > "$dir/serial"
    [ -f "$dir/crlnumber" ] || echo 1000 > "$dir/crlnumber"
}

slugify() {
    printf '%s' "$1" \
        | tr '[:upper:]' '[:lower:]' \
        | sed -e 's/[^a-z0-9]\+/-/g' -e 's/^-//' -e 's/-$//'
}
