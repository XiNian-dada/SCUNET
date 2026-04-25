#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${HOME}/.local/share/scunet-autologin"
SERVICE_DIR="${HOME}/.config/systemd/user"
SERVICE_FILE="${SERVICE_DIR}/scunet-autologin.service"
SCRIPT_TARGET="${APP_DIR}/scunet_autologin.py"
CONFIG_TARGET="${APP_DIR}/config.json"

PYTHON_BIN="$(command -v python3 || true)"
if [[ -z "${PYTHON_BIN}" ]]; then
  echo "python3 was not found."
  exit 1
fi

mkdir -p "${APP_DIR}" "${SERVICE_DIR}"
cp "${SCRIPT_DIR}/scunet_autologin.py" "${SCRIPT_TARGET}"
chmod +x "${SCRIPT_TARGET}"

if [[ ! -f "${CONFIG_TARGET}" ]]; then
  cp "${SCRIPT_DIR}/config.example.json" "${CONFIG_TARGET}"
  echo "Created config template at:"
  echo "  ${CONFIG_TARGET}"
fi

cat > "${SERVICE_FILE}" <<EOF
[Unit]
Description=SCUNET Autologin
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=${PYTHON_BIN} ${SCRIPT_TARGET} --config ${CONFIG_TARGET}
Restart=on-failure
RestartSec=5
WorkingDirectory=${APP_DIR}

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now scunet-autologin.service

echo "systemd user service installed."
echo "Config file:"
echo "  ${CONFIG_TARGET}"
echo
echo "Next steps:"
echo "  1. Edit ${CONFIG_TARGET}"
echo "  2. Set password_source to env, file, or command"
echo "  3. Check status with: systemctl --user status scunet-autologin.service"
