#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
APP_SUPPORT_DIR="${HOME}/Library/Application Support/SCUNETAutologin"
CONFIG_TARGET="${APP_SUPPORT_DIR}/config.json"
NETWORKSETUP_BIN="/usr/sbin/networksetup"

detect_wifi_interface() {
  "${NETWORKSETUP_BIN}" -listallhardwareports | awk '
    $0 == "Hardware Port: Wi-Fi" { want = 1; next }
    want && $0 ~ /^Device: / { sub(/^Device: /, "", $0); print; exit }
  '
}

normalize_service() {
  case "${1:l}" in
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
INTERFACE="${3:-$(detect_wifi_interface)}"

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

if [[ -z "${INTERFACE}" ]]; then
  echo "Could not auto-detect the Wi-Fi interface. Please pass it explicitly, for example:"
  echo "  $0 ${CAMPUS_USERNAME} ${SERVICE} en0"
  exit 1
fi

mkdir -p "${APP_SUPPORT_DIR}"

cat > "${CONFIG_TARGET}" <<EOF
{
  "username": "${CAMPUS_USERNAME}",
  "service": "${SERVICE}",
  "interface": "${INTERFACE}"
}
EOF

echo "Wrote macOS config:"
echo "  ${CONFIG_TARGET}"
echo "Using service:"
echo "  ${SERVICE}"
echo "Using Wi-Fi interface:"
echo "  ${INTERFACE}"
echo

"${SCRIPT_DIR}/set_keychain_password.sh"
"${SCRIPT_DIR}/install_launch_agent.sh"

echo
echo "SCUNET autologin is configured."
echo "It will only react to the SSID SCUNET and will check roughly once per second."
