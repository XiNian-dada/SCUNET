# SCUNET Autologin Service

This repo recreates the core flow used by `SCU-Network-Helper-For-Mac` and now intentionally targets `macOS` only.

## Behavior

- It only reacts when the Mac is on `SCUNET`.
- It checks roughly once per second.
- When the portal indicates login is required, it posts the SCUNET login request automatically.
- Passwords are stored in macOS Keychain.
- Logs rotate automatically at `~/Library/Logs/SCUNETAutologin/daemon.log`.
- It uses `launchd` as the background service wrapper.

## Files

- `scunet_autologin.py`: macOS auto-login daemon.
- `config.example.json`: minimal config template.
- `setup_macos.sh`: one-step setup on macOS.
- `install_launch_agent.sh`: install the LaunchAgent.
- `set_keychain_password.sh`: store the SCUNET password in Keychain.
- `uninstall_launch_agent.sh`: remove the LaunchAgent.

## Quick start

```bash
cd /Users/bernard/Code/SCUNET
./setup_macos.sh YOUR_STUDENT_ID EDUNET en0
```

If your Wi-Fi interface is auto-detected correctly, the last `en0` argument can be omitted:

```bash
cd /Users/bernard/Code/SCUNET
./setup_macos.sh YOUR_STUDENT_ID EDUNET
```

Supported services:

- `EDUNET`
- `CHINATELECOM`
- `CHINAMOBILE`
- `CHINAUNICOM`

## Config

The minimal config is:

```json
{
  "username": "YOUR_STUDENT_ID",
  "service": "EDUNET",
  "interface": "en0"
}
```

The real config file lives at:

```text
~/Library/Application Support/SCUNETAutologin/config.json
```

## Logs and status

Follow the rotating app log:

```bash
tail -f ~/Library/Logs/SCUNETAutologin/daemon.log
```

Check the LaunchAgent:

```bash
launchctl print gui/$(id -u)/com.scunet.autologin
```

Validate config:

```bash
python3 /Users/bernard/Code/SCUNET/scunet_autologin.py --config ~/Library/Application\ Support/SCUNETAutologin/config.json --check-config
```

Run one dry-run cycle:

```bash
python3 /Users/bernard/Code/SCUNET/scunet_autologin.py --config ~/Library/Application\ Support/SCUNETAutologin/config.json --once --dry-run --verbose
```

## Uninstall

```bash
cd /Users/bernard/Code/SCUNET
./uninstall_launch_agent.sh
```

## Validation status

- macOS: exercised locally during development, including launchd flow, config generation, logging, retry tuning, and notification handling.
