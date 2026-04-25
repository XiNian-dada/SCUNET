#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME}/.local/share/scunet-autologin"
CONFIG_TARGET="${APP_DIR}/config.json"
PASSWORD_TARGET="${APP_DIR}/password.txt"

normalize_service() {
  case "${1,,}" in
    edunet|campus|campusnet|scunet|校园网)
      echo "EDUNET"
      ;;
    telecom|chinatelecom|dianxin|电信)
      echo "CHINATELECOM"
      ;;
    mobile|chinamobile|yidong|移动)
      echo "CHINAMOBILE"
      ;;
    unicom|chinaunicom|liantong|联通)
      echo "CHINAUNICOM"
      ;;
    *)
      return 1
      ;;
  esac
}

CAMPUS_USERNAME="${1:-}"
RAW_SERVICE="${2:-EDUNET}"
INTERFACE="${3:-wlan0}"

if [[ -z "${CAMPUS_USERNAME}" ]]; then
  echo "Usage: $0 <username> [service] [interface]"
  echo "Service: EDUNET | CHINATELECOM | CHINAMOBILE | CHINAUNICOM"
  exit 1
fi

if ! SERVICE="$(normalize_service "${RAW_SERVICE}")"; then
  echo "Unsupported service: ${RAW_SERVICE}"
  echo "Use one of: EDUNET, CHINATELECOM, CHINAMOBILE, CHINAUNICOM"
  exit 1
fi

mkdir -p "${APP_DIR}"

read -r -s -p "SCUNET password: " CAMPUS_PASSWORD
echo

if [[ -z "${CAMPUS_PASSWORD}" ]]; then
  echo "Password cannot be empty."
  exit 1
fi

printf '%s\n' "${CAMPUS_PASSWORD}" > "${PASSWORD_TARGET}"
chmod 600 "${PASSWORD_TARGET}"

cat > "${CONFIG_TARGET}" <<EOF
{
  "username": "${CAMPUS_USERNAME}",
  "service": "${SERVICE}",
  "interface": "${INTERFACE}",
  "password_source": "file",
  "password_file": "${PASSWORD_TARGET}"
}
EOF

echo "Wrote Linux config:"
echo "  ${CONFIG_TARGET}"
echo "Using service:"
echo "  ${SERVICE}"
echo "Using interface:"
echo "  ${INTERFACE}"
echo "Password file:"
echo "  ${PASSWORD_TARGET}"
echo

"${SCRIPT_DIR}/install_systemd_user_service.sh"

echo
echo "SCUNET autologin is configured for Linux."

