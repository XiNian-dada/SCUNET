# SCUNET Autologin Service

This repo recreates the core flow used by `SCU-Network-Helper-For-Mac`, but refactors it into a cross-platform Python daemon.

## What is portable

The core authentication flow is portable:

1. Detect whether the target adapter is active.
2. If the adapter is Wi-Fi, verify that the SSID is `SCUNET`.
3. Use a bound `ping` when the platform supports it.
4. If the probe fails, fetch `http://192.168.2.135/`, extract `queryString`, and post the login request.

The service wrapper is platform-specific:

- macOS: `launchd`
- Linux: `systemd --user`
- Windows: Task Scheduler at logon

## Platform notes

- macOS is the most complete path: interface binding and Keychain are both supported.
- Linux is workable: interface binding is supported, but Wi-Fi SSID detection depends on `iwgetid`, `iw`, or `nmcli`.
- Windows is workable with one limitation from curl: according to the official curl docs, `--interface` does not support interface names on Windows, so `login_bind` should be left empty or set to a local adapter IP instead of an interface alias.

## macOS behavior

The default macOS path is now intentionally strict and simple:

- It only reacts to Wi-Fi SSID `SCUNET`.
- It does not attempt login when the Wi-Fi name is unknown.
- If the SSID cannot be read on a given Mac, it falls back to checking whether the SCUNET portal is actually present on the network.
- It checks roughly once per second, so connecting to `SCUNET` should trigger login almost immediately.
- The minimal config only needs `username`, `service`, and `interface`.
- The app log rotates automatically at `~/Library/Logs/SCUNETAutologin/daemon.log`.

## Password sources

The daemon supports these password sources:

- `macos_keychain`: macOS only.
- `env`: read from an environment variable.
- `file`: read from a local file.
- `command`: run a command and read the password from stdout.

For cross-platform use, `file` or `env` is usually the easiest.

## Files

- `scunet_autologin.py`: cross-platform daemon.
- `config.example.json`: shared config template.
- `setup_macos.sh`: one-step macOS setup.
- `setup_linux.sh`: one-step Linux setup.
- `setup_windows.ps1`: one-step Windows setup.
- `install_launch_agent.sh`: install on macOS.
- `set_keychain_password.sh`: store password in macOS Keychain.
- `install_systemd_user_service.sh`: install on Linux.
- `install_windows_task.ps1`: install on Windows.
- `uninstall_launch_agent.sh`: remove the macOS LaunchAgent.
- `uninstall_systemd_user_service.sh`: remove the Linux user service.
- `uninstall_windows_task.ps1`: remove the Windows scheduled task.

## Quick start on macOS

```bash
cd /Users/bernard/Code/SCUNET
./setup_macos.sh YOUR_STUDENT_ID EDUNET
```

If Wi-Fi interface auto-detection is wrong, pass it explicitly:

```bash
cd /Users/bernard/Code/SCUNET
./setup_macos.sh YOUR_STUDENT_ID EDUNET en0
```

## Quick start on Linux

```bash
cd /path/to/SCUNET
./setup_linux.sh YOUR_STUDENT_ID EDUNET wlan0
```

If your wireless interface is named differently, replace `wlan0` with something like `wlp2s0`.

The Linux helper stores the password in:

```text
~/.local/share/scunet-autologin/password.txt
```

with `600` permissions and installs a `systemd --user` service.

## Quick start on Windows

Open PowerShell in the project directory and run:

```powershell
.\setup_windows.ps1 -Username YOUR_STUDENT_ID -Service EDUNET -Interface "Wi-Fi"
```

The Windows helper stores the password in:

```text
%APPDATA%\SCUNETAutologin\password.txt
```

and registers a Task Scheduler job that starts at logon.

## Uninstall

macOS:

```bash
cd /Users/bernard/Code/SCUNET
./uninstall_launch_agent.sh
```

Linux:

```bash
cd /path/to/SCUNET
./uninstall_systemd_user_service.sh
```

Windows:

```powershell
.\uninstall_windows_task.ps1
```

## Example config choices

macOS:

- `service`: `EDUNET`, `CHINATELECOM`, `CHINAMOBILE`, or `CHINAUNICOM`
- `interface`: `en0`
- `password_source`: `macos_keychain`

Linux:

- `service`: `EDUNET`, `CHINATELECOM`, `CHINAMOBILE`, or `CHINAUNICOM`
- `interface`: `wlan0` or `wlp2s0`
- `password_source`: `file` or `env`

Windows:

- `service`: `EDUNET`, `CHINATELECOM`, `CHINAMOBILE`, or `CHINAUNICOM`
- `interface`: `Wi-Fi`
- `login_bind`: empty string, or a local adapter IPv4 address
- `ping_bind`: empty string
- `password_source`: `file` or `env`

## Validation

Check config:

```bash
python3 /Users/bernard/Code/SCUNET/scunet_autologin.py --config /path/to/config.json --check-config
```

Run one dry-run cycle:

```bash
python3 /Users/bernard/Code/SCUNET/scunet_autologin.py --config /path/to/config.json --once --dry-run --verbose
```

## Validation status

- macOS: exercised locally during development, including launchd flow, config generation, and logging fixes.
- Linux: shell scripts and Python paths were syntax-checked locally, but not run on a Linux host in this session.
- Windows: PowerShell scripts were written for Task Scheduler flow, but not run on a Windows host in this session.
