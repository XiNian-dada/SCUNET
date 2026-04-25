#!/bin/zsh
set -euo pipefail

SERVICE_NAME="${1:-SCUNETAutologin}"
ACCOUNT_NAME="${2:-campus-network-password}"

read -r -s "PASSWORD?SCUNET password: "
echo

if [[ -z "${PASSWORD}" ]]; then
  echo "Password cannot be empty."
  exit 1
fi

/usr/bin/security add-generic-password \
  -U \
  -a "${ACCOUNT_NAME}" \
  -s "${SERVICE_NAME}" \
  -w "${PASSWORD}" >/dev/null

echo "Password saved to Keychain."
