#!/bin/zsh
set -euo pipefail

LABEL="com.scunet.autologin"
PLIST_TARGET="${HOME}/Library/LaunchAgents/${LABEL}.plist"

/bin/launchctl bootout "gui/${UID}" "${PLIST_TARGET}" >/dev/null 2>&1 || true
rm -f "${PLIST_TARGET}"

echo "LaunchAgent removed."
echo "Config and logs were left in:"
echo "  ${HOME}/Library/Application Support/SCUNETAutologin"
echo "  ${HOME}/Library/Logs/SCUNETAutologin"
