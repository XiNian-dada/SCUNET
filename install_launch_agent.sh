#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="${0:A:h}"
LABEL="com.scunet.autologin"
APP_SUPPORT_DIR="${HOME}/Library/Application Support/SCUNETAutologin"
LOG_DIR="${HOME}/Library/Logs/SCUNETAutologin"
PLIST_DIR="${HOME}/Library/LaunchAgents"
SCRIPT_TARGET="${APP_SUPPORT_DIR}/scunet_autologin.py"
CONFIG_TARGET="${APP_SUPPORT_DIR}/config.json"
PLIST_TARGET="${PLIST_DIR}/${LABEL}.plist"

detect_wifi_interface() {
  /usr/sbin/networksetup -listallhardwareports | awk '
    $0 == "Hardware Port: Wi-Fi" { want = 1; next }
    want && $0 ~ /^Device: / { sub(/^Device: /, "", $0); print; exit }
  '
}

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 was not found."
  exit 1
fi

mkdir -p "${APP_SUPPORT_DIR}" "${LOG_DIR}" "${PLIST_DIR}"

cp "${SCRIPT_DIR}/scunet_autologin.py" "${SCRIPT_TARGET}"
chmod +x "${SCRIPT_TARGET}"

if [[ ! -f "${CONFIG_TARGET}" ]]; then
  DEFAULT_INTERFACE="$(detect_wifi_interface)"
  if [[ -z "${DEFAULT_INTERFACE}" ]]; then
    DEFAULT_INTERFACE="en0"
  fi
  cat > "${CONFIG_TARGET}" <<EOF
{
  "username": "PUT_YOUR_STUDENT_ID_HERE",
  "service": "EDUNET",
  "interface": "${DEFAULT_INTERFACE}"
}
EOF
  echo "Created config template at:"
  echo "  ${CONFIG_TARGET}"
fi

cat > "${PLIST_TARGET}" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>${LABEL}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${PYTHON_BIN}</string>
    <string>${SCRIPT_TARGET}</string>
    <string>--config</string>
    <string>${CONFIG_TARGET}</string>
  </array>
  <key>RunAtLoad</key>
  <true/>
  <key>KeepAlive</key>
  <true/>
  <key>ProcessType</key>
  <string>Background</string>
  <key>WorkingDirectory</key>
  <string>${APP_SUPPORT_DIR}</string>
  <key>EnvironmentVariables</key>
  <dict>
    <key>PYTHONUNBUFFERED</key>
    <string>1</string>
  </dict>
  <key>StandardOutPath</key>
  <string>${LOG_DIR}/stdout.log</string>
  <key>StandardErrorPath</key>
  <string>${LOG_DIR}/stderr.log</string>
</dict>
</plist>
EOF

/bin/launchctl bootout "gui/${UID}" "${PLIST_TARGET}" >/dev/null 2>&1 || true
/bin/launchctl bootstrap "gui/${UID}" "${PLIST_TARGET}"
/bin/launchctl kickstart -k "gui/${UID}/${LABEL}"

echo "LaunchAgent installed."
echo "Config file:"
echo "  ${CONFIG_TARGET}"
echo "App log (rotates automatically):"
echo "  ${LOG_DIR}/daemon.log"
echo "launchd fallback logs:"
echo "  ${LOG_DIR}/stdout.log"
echo "  ${LOG_DIR}/stderr.log"
echo
echo "Next steps:"
echo "  1. Edit ${CONFIG_TARGET}"
echo "  2. Run ${SCRIPT_DIR}/set_keychain_password.sh"
echo "  3. Check status with: launchctl print gui/${UID}/${LABEL}"
