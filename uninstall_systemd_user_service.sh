#!/usr/bin/env bash
set -euo pipefail

SERVICE_FILE="${HOME}/.config/systemd/user/scunet-autologin.service"

systemctl --user disable --now scunet-autologin.service >/dev/null 2>&1 || true
rm -f "${SERVICE_FILE}"
systemctl --user daemon-reload

echo "systemd user service removed."
echo "Config and password were left in:"
echo "  ${HOME}/.local/share/scunet-autologin"

