#!/usr/bin/env bash
# Prints the DER sha256 fingerprint of the committed Marquee public key and
# fails unless it equals MARQUEE_KEY_FINGERPRINT (for example
# sha256:48ba24e6f91da7ced7540b1512d0eac37b7fa070d474d28b9f8a89a0c5955242).
#
# Usage: MARQUEE_KEY_FINGERPRINT=sha256:<hex> scripts/check-key.sh [pem-file]
#
# The key file is public. Nothing here reads or prints a secret.
set -euo pipefail

pem="${1:-marquee-publisher-key.pub.pem}"
expected="${MARQUEE_KEY_FINGERPRINT:-}"

if [ -z "$expected" ]; then
  echo "check-key: MARQUEE_KEY_FINGERPRINT is not set" >&2
  exit 2
fi
if [ ! -f "$pem" ]; then
  echo "check-key: $pem not found" >&2
  exit 2
fi

if command -v sha256sum >/dev/null 2>&1; then
  hash() { sha256sum | cut -d' ' -f1; }
else
  hash() { shasum -a 256 | cut -d' ' -f1; }
fi

actual="sha256:$(openssl pkey -pubin -in "$pem" -outform DER | hash)"
echo "$pem fingerprint: $actual"

if [ "$actual" != "$expected" ]; then
  echo "check-key: MISMATCH, expected $expected" >&2
  exit 1
fi
echo "check-key: ok"
