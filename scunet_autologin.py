#!/usr/bin/env python3

import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
import platform
import re
import shlex
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, fields
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple, Union
from urllib.parse import quote, quote_plus


SYSTEM = platform.system().lower()
IS_MACOS = SYSTEM == "darwin"
IS_LINUX = SYSTEM == "linux"
IS_WINDOWS = SYSTEM == "windows"

DEFAULT_CONFIG_NAME = "config.json"
DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36"
)
SUCCESS_URL_MARKERS = ("success.jsp", "redirectortosuccess.jsp")
SERVICE_CODES = {
    "CHINATELECOM": "%25E7%2594%25B5%25E4%25BF%25A1%25E5%2587%25BA%25E5%258F%25A3",
    "CHINAMOBILE": "%25E7%25A7%25BB%25E5%258A%25A8%25E5%2587%25BA%25E5%258F%25A3",
    "CHINAUNICOM": "%25E8%2581%2594%25E9%2580%259A%25E5%2587%25BA%25E5%258F%25A3",
    "EDUNET": "internet",
}
PASSWORD_SOURCES = {"macos_keychain", "env", "command", "file"}


def resolve_command(candidates: Sequence[str]) -> str:
    for candidate in candidates:
        if Path(candidate).is_absolute() and Path(candidate).exists():
            return candidate
        resolved = shutil.which(candidate)
        if resolved:
            return resolved
    return candidates[0]


CURL_BIN = resolve_command(
    [
        "/usr/bin/curl",
        "/usr/local/bin/curl",
        "/opt/homebrew/bin/curl",
        r"C:\Windows\System32\curl.exe",
        "curl",
    ]
)
PING_BIN = resolve_command(
    [
        "/sbin/ping",
        "/usr/sbin/ping",
        "/bin/ping",
        r"C:\Windows\System32\PING.EXE",
        "ping",
    ]
)
IFCONFIG_BIN = resolve_command(
    ["/sbin/ifconfig", "/usr/sbin/ifconfig", "ifconfig"]
)
IPCONFIG_BIN = resolve_command(
    ["/usr/sbin/ipconfig", "/sbin/ipconfig", "ipconfig"]
)
NETWORKSETUP_BIN = resolve_command(
    ["/usr/sbin/networksetup", "networksetup"]
)
SECURITY_BIN = resolve_command(
    ["/usr/bin/security", "security"]
)
IP_BIN = resolve_command(
    ["/usr/sbin/ip", "/sbin/ip", "/usr/bin/ip", "ip"]
)
IW_BIN = resolve_command(
    ["/usr/sbin/iw", "/sbin/iw", "/usr/bin/iw", "iw"]
)
IWGETID_BIN = resolve_command(
    ["/usr/sbin/iwgetid", "/sbin/iwgetid", "/usr/bin/iwgetid", "iwgetid"]
)
NMCLI_BIN = resolve_command(
    ["/usr/bin/nmcli", "/usr/sbin/nmcli", "nmcli"]
)
NETSH_BIN = resolve_command(
    [r"C:\Windows\System32\netsh.exe", "netsh"]
)


class ConfigError(RuntimeError):
    pass


class CommandError(RuntimeError):
    pass


def expand_path(value: str) -> Path:
    return Path(os.path.expandvars(os.path.expanduser(value))).resolve()


def split_command(value: Union[str, List[str]]) -> List[str]:
    if isinstance(value, list):
        return [str(part) for part in value]
    if isinstance(value, str):
        return shlex.split(value, posix=not IS_WINDOWS)
    raise ConfigError("password_command must be a JSON string or array")


def looks_like_bind_address(value: str) -> bool:
    if not value or " " in value:
        return False
    if re.fullmatch(r"\d+\.\d+\.\d+\.\d+", value):
        return True
    if ":" in value:
        return True
    return "." in value


def decode_unicode_escapes(value: str) -> str:
    try:
        return json.loads(f'"{value}"')
    except json.JSONDecodeError:
        return value


def extract_query_string(html: str) -> Optional[str]:
    parts = html.split("/index.jsp?", 1)
    if len(parts) != 2:
        return None
    raw_query = parts[1].split("'</script>", 1)[0]
    if not raw_query:
        return None
    return quote(raw_query, safe="")


def extract_error_message(body: str) -> Optional[str]:
    match = re.search(r'"message":"(.*?)"', body)
    if not match:
        return None
    return decode_unicode_escapes(match.group(1))


@dataclass
class Config:
    username: str
    service: str = "EDUNET"
    target_ssid: str = "SCUNET"
    detect_interface: str = "PUT_INTERFACE_NAME_HERE"
    login_bind: str = ""
    ping_bind: str = ""
    ping_address: str = "222.220.212.130"
    probe_url: str = "http://192.168.2.135/"
    auto_login_enabled: bool = True
    allow_unknown_wifi_name: bool = False
    poll_interval_seconds: int = 1
    idle_interval_seconds: int = 1
    connect_timeout_seconds: int = 3
    curl_max_time_seconds: int = 5
    min_login_interval_seconds: int = 15
    password_source: str = ""
    password_env: str = "SCUNET_PASSWORD"
    password_command: Union[str, List[str], None] = None
    password_file: str = ""
    keychain_service: str = "SCUNETAutologin"
    keychain_account: str = "campus-network-password"

    @classmethod
    def from_dict(cls, raw: Dict[str, object]) -> "Config":
        normalized = dict(raw)

        if normalized.get("interface"):
            interface = normalized["interface"]
            normalized.setdefault("detect_interface", interface)
            normalized.setdefault("login_bind", interface)
            normalized.setdefault("ping_bind", interface)

        if normalized.get("ssid") and not normalized.get("target_ssid"):
            normalized["target_ssid"] = normalized["ssid"]

        if normalized.get("login_interface") and not normalized.get("login_bind"):
            normalized["login_bind"] = normalized["login_interface"]
        if normalized.get("ping_interface") and not normalized.get("ping_bind"):
            normalized["ping_bind"] = normalized["ping_interface"]
        if "password_source" not in normalized:
            normalized["password_source"] = "macos_keychain" if IS_MACOS else "env"

        dataclass_keys = {field.name for field in fields(cls)}
        clean = {key: normalized[key] for key in normalized if key in dataclass_keys}
        config = cls(**clean)

        if not config.login_bind:
            config.login_bind = config.detect_interface
        if not config.ping_bind:
            config.ping_bind = config.detect_interface
        return config

    def validate(self) -> None:
        if not self.username or self.username.startswith("PUT_"):
            raise ConfigError("config.username is missing or still uses the placeholder value")
        if not self.detect_interface or self.detect_interface.startswith("PUT_"):
            raise ConfigError(
                "config.detect_interface is missing or still uses the placeholder value"
            )
        if self.service not in SERVICE_CODES:
            raise ConfigError(
                f"config.service must be one of: {', '.join(sorted(SERVICE_CODES))}"
            )
        if not self.ping_address:
            raise ConfigError("config.ping_address is required")
        if not self.probe_url:
            raise ConfigError("config.probe_url is required")
        if self.poll_interval_seconds <= 0:
            raise ConfigError("config.poll_interval_seconds must be > 0")
        if self.idle_interval_seconds <= 0:
            raise ConfigError("config.idle_interval_seconds must be > 0")
        if self.min_login_interval_seconds < 0:
            raise ConfigError("config.min_login_interval_seconds must be >= 0")
        if self.password_source not in PASSWORD_SOURCES:
            raise ConfigError(
                f"config.password_source must be one of: {', '.join(sorted(PASSWORD_SOURCES))}"
            )
        if self.password_source == "macos_keychain" and not IS_MACOS:
            raise ConfigError("password_source=macos_keychain is only supported on macOS")
        if self.password_source == "env" and not self.password_env:
            raise ConfigError("config.password_env is required for password_source=env")
        if self.password_source == "command" and not self.password_command:
            raise ConfigError("config.password_command is required for password_source=command")
        if self.password_source == "file" and not self.password_file:
            raise ConfigError("config.password_file is required for password_source=file")


def load_config(path: Path) -> Config:
    if not path.exists():
        raise ConfigError(f"config file not found: {path}")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"config file is not valid JSON: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config root must be a JSON object")
    config = Config.from_dict(raw)
    config.validate()
    return config


def default_log_path() -> Path:
    if IS_MACOS:
        return expand_path("~/Library/Logs/SCUNETAutologin/daemon.log")
    if IS_LINUX:
        return expand_path("~/.local/state/scunet-autologin/daemon.log")
    if IS_WINDOWS:
        appdata = os.environ.get("APPDATA", "~")
        return expand_path(f"{appdata}/SCUNETAutologin/daemon.log")
    return expand_path("~/scunet-autologin.log")


def setup_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.setLevel(level)

    log_path = default_log_path()
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        rotating_handler = RotatingFileHandler(
            log_path,
            maxBytes=1_000_000,
            backupCount=3,
            encoding="utf-8",
        )
        rotating_handler.setFormatter(formatter)
        root_logger.addHandler(rotating_handler)
    except OSError:
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)
        root_logger.warning("unable to open rotating log file at %s; using stderr only", log_path)
        return

    if sys.stderr.isatty():
        stream_handler = logging.StreamHandler()
        stream_handler.setFormatter(formatter)
        root_logger.addHandler(stream_handler)


def run_command(
    args: Sequence[str],
    *,
    timeout: Optional[int] = None,
    capture_output: bool = True,
) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(
            list(args),
            check=False,
            timeout=timeout,
            capture_output=capture_output,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except subprocess.TimeoutExpired as exc:
        raise CommandError(f"command timed out: {' '.join(args)}") from exc
    except OSError as exc:
        raise CommandError(f"unable to run command: {' '.join(args)}: {exc}") from exc


def build_post_body(config: Config, password: str, query_string: str) -> str:
    parts = [
        f"userId={quote_plus(config.username, safe='')}",
        f"password={quote_plus(password, safe='')}",
        f"service={SERVICE_CODES[config.service]}",
        f"queryString={query_string}",
        "operatorPwd=",
        "operatorUserId=",
        "validcode=",
        "passwordEncrypt=false",
    ]
    return "&".join(parts)


def read_keychain_password(config: Config) -> Optional[str]:
    result = run_command(
        [
            SECURITY_BIN,
            "find-generic-password",
            "-w",
            "-s",
            config.keychain_service,
            "-a",
            config.keychain_account,
        ],
        timeout=5,
    )
    if result.returncode != 0:
        return None
    password = result.stdout.strip()
    return password or None


def read_password(config: Config) -> Optional[str]:
    if config.password_source == "macos_keychain":
        return read_keychain_password(config)

    if config.password_source == "env":
        password = os.environ.get(config.password_env, "").strip()
        return password or None

    if config.password_source == "file":
        password_path = expand_path(config.password_file)
        if not password_path.exists():
            return None
        password = password_path.read_text(encoding="utf-8").strip()
        return password or None

    if config.password_source == "command":
        command = split_command(config.password_command)
        result = run_command(command, timeout=5)
        if result.returncode != 0:
            return None
        password = result.stdout.strip()
        return password or None

    raise ConfigError(f"unsupported password source: {config.password_source}")


class SCUNETAutologinDaemon:
    def __init__(self, config_path: Path):
        self.config_path = config_path
        self.stop_requested = False
        self.last_state: Optional[str] = None
        self.last_login_attempt_at = 0.0
        self.hardware_ports: Dict[str, str] = {}
        self.hardware_ports_loaded_at = 0.0
        self.macos_wifi_interfaces: Dict[str, bool] = {}
        self.macos_wifi_interfaces_loaded_at = 0.0

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, signum: int, _frame) -> None:
        logging.info("received signal %s, stopping", signum)
        self.stop_requested = True

    def set_state(self, state: str) -> None:
        if state != self.last_state:
            logging.info("%s", state)
            self.last_state = state

    def sleep_interruptibly(self, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not self.stop_requested and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.1, deadline - time.monotonic())))

    def refresh_hardware_ports(self, force: bool = False) -> None:
        if not IS_MACOS:
            return

        now = time.monotonic()
        if not force and now - self.hardware_ports_loaded_at < 300:
            return

        result = run_command([NETWORKSETUP_BIN, "-listallhardwareports"], timeout=5)
        if result.returncode != 0:
            logging.debug("networksetup hardware port lookup unavailable on this Mac")
            self.hardware_ports = {}
            self.hardware_ports_loaded_at = now
            return

        current_port = None
        mapping: Dict[str, str] = {}
        for line in result.stdout.splitlines():
            stripped = line.strip()
            if stripped.startswith("Hardware Port: "):
                current_port = stripped.split(": ", 1)[1]
            elif stripped.startswith("Device: ") and current_port:
                device = stripped.split(": ", 1)[1]
                mapping[device] = current_port

        self.hardware_ports = mapping
        self.hardware_ports_loaded_at = now

    def refresh_macos_wifi_interfaces(self, force: bool = False) -> None:
        if not IS_MACOS:
            return

        now = time.monotonic()
        if not force and now - self.macos_wifi_interfaces_loaded_at < 300:
            return

        result = run_command(
            ["/usr/sbin/system_profiler", "SPAirPortDataType", "-json"],
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            self.macos_wifi_interfaces = {}
            self.macos_wifi_interfaces_loaded_at = now
            return

        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError:
            self.macos_wifi_interfaces = {}
            self.macos_wifi_interfaces_loaded_at = now
            return

        names: Dict[str, bool] = {}
        for top_level in payload.get("SPAirPortDataType", []):
            interfaces = top_level.get("spairport_airport_interfaces", [])
            for iface in interfaces:
                name = iface.get("_name")
                if isinstance(name, str) and name:
                    names[name] = True

        self.macos_wifi_interfaces = names
        self.macos_wifi_interfaces_loaded_at = now

    def interface_hardware_port(self, interface: str) -> Optional[str]:
        self.refresh_hardware_ports()
        return self.hardware_ports.get(interface)

    def linux_interface_is_active(self, interface: str) -> bool:
        operstate = Path("/sys/class/net") / interface / "operstate"
        if operstate.exists():
            state = operstate.read_text(encoding="utf-8").strip().lower()
            if state in {"up", "unknown"}:
                return True
            if state in {"down", "dormant", "notpresent"}:
                return False

        result = run_command([IP_BIN, "link", "show", "dev", interface], timeout=5)
        if result.returncode != 0:
            return False
        output = result.stdout
        return "LOWER_UP" in output or "state UP" in output

    def windows_interface_rows(self) -> List[Dict[str, str]]:
        result = run_command([NETSH_BIN, "interface", "show", "interface"], timeout=5)
        if result.returncode != 0:
            return []

        rows: List[Dict[str, str]] = []
        for line in result.stdout.splitlines():
            if "Admin State" in line or "---" in line:
                continue
            if not line.strip():
                continue
            parts = re.split(r"\s{2,}", line.strip())
            if len(parts) < 4:
                continue
            rows.append(
                {
                    "admin_state": parts[0],
                    "state": parts[1],
                    "type": parts[2],
                    "name": parts[3],
                }
            )
        return rows

    def windows_wlan_blocks(self) -> List[Dict[str, str]]:
        result = run_command([NETSH_BIN, "wlan", "show", "interfaces"], timeout=5)
        if result.returncode != 0:
            return []

        blocks: List[Dict[str, str]] = []
        current: Dict[str, str] = {}
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line:
                if current:
                    blocks.append(current)
                    current = {}
                continue
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            current[key.strip().lower()] = value.strip()

        if current:
            blocks.append(current)
        return blocks

    def find_windows_wlan_block(self, interface: str) -> Optional[Dict[str, str]]:
        for block in self.windows_wlan_blocks():
            if block.get("name") == interface:
                return block
        return None

    def is_wifi_interface(self, interface: str) -> bool:
        if IS_MACOS:
            self.refresh_macos_wifi_interfaces()
            if interface in self.macos_wifi_interfaces:
                return True

            port = self.interface_hardware_port(interface)
            if not port:
                return False
            normalized = port.lower()
            return "wi-fi" in normalized or "airport" in normalized

        if IS_LINUX:
            base = Path("/sys/class/net") / interface
            return (base / "wireless").exists() or (base / "phy80211").exists()

        if IS_WINDOWS:
            return self.find_windows_wlan_block(interface) is not None

        return False

    def interface_is_active(self, interface: str) -> bool:
        if IS_MACOS:
            result = run_command([IFCONFIG_BIN, interface], timeout=5)
            if result.returncode != 0:
                return False
            if "status: active" in result.stdout:
                return True
            addr_result = run_command([IPCONFIG_BIN, "getifaddr", interface], timeout=5)
            return addr_result.returncode == 0 and bool(addr_result.stdout.strip())

        if IS_LINUX:
            return self.linux_interface_is_active(interface)

        if IS_WINDOWS:
            wlan_block = self.find_windows_wlan_block(interface)
            if wlan_block is not None:
                return wlan_block.get("state", "").lower() == "connected"
            for row in self.windows_interface_rows():
                if row["name"] == interface:
                    return row["state"].lower() == "connected"
            return False

        raise ConfigError(f"unsupported platform: {SYSTEM}")

    def current_ssid(self, interface: str) -> Optional[str]:
        if IS_MACOS:
            result = run_command([NETWORKSETUP_BIN, "-getairportnetwork", interface], timeout=5)
            output = (result.stdout or "").strip()
            if result.returncode != 0 and not output:
                return None
            prefix = "Current Wi-Fi Network: "
            if output.startswith(prefix):
                ssid = output[len(prefix) :].strip()
                return ssid or None
            if "AuthorizationCreate() failed" in output:
                return None
            return None

        if IS_LINUX:
            if shutil.which(IWGETID_BIN):
                result = run_command([IWGETID_BIN, "-r", interface], timeout=5)
                if result.returncode == 0 and result.stdout.strip():
                    return result.stdout.strip()

            if shutil.which(IW_BIN):
                result = run_command([IW_BIN, "dev", interface, "link"], timeout=5)
                if result.returncode == 0:
                    match = re.search(r"SSID:\s*(.+)", result.stdout)
                    if match:
                        return match.group(1).strip()

            if shutil.which(NMCLI_BIN):
                result = run_command(
                    [NMCLI_BIN, "-t", "-f", "DEVICE,ACTIVE,SSID", "dev", "wifi"],
                    timeout=5,
                )
                if result.returncode == 0:
                    for line in result.stdout.splitlines():
                        parts = line.split(":", 2)
                        if len(parts) != 3:
                            continue
                        device, active, ssid = parts
                        if device == interface and active == "yes":
                            return ssid or None
            return None

        if IS_WINDOWS:
            wlan_block = self.find_windows_wlan_block(interface)
            if not wlan_block:
                return None
            ssid = wlan_block.get("ssid")
            return ssid or None

        return None

    def ping_is_alive(self, address: str, bind: str) -> bool:
        if IS_MACOS:
            args = [PING_BIN, "-c", "1", "-W", "2000"]
            if bind:
                args.extend(["-b", bind])
            args.append(address)
            result = run_command(args, timeout=5, capture_output=False)
            return result.returncode == 0

        if IS_LINUX:
            args = [PING_BIN, "-c", "1", "-W", "2"]
            if bind:
                args.extend(["-I", bind])
            args.append(address)
            result = run_command(args, timeout=5, capture_output=False)
            return result.returncode == 0

        if IS_WINDOWS:
            args = [PING_BIN, "-n", "1", "-w", "2000", address]
            result = run_command(args, timeout=5, capture_output=False)
            return result.returncode == 0

        raise ConfigError(f"unsupported platform: {SYSTEM}")

    def run_curl(
        self,
        url: str,
        *,
        bind: str,
        connect_timeout_seconds: int,
        max_time_seconds: int,
        post_data: Optional[str],
    ) -> Tuple[str, str]:
        args = [
            CURL_BIN,
            "-s",
            "-L",
            "--noproxy",
            "*",
            "--connect-timeout",
            str(connect_timeout_seconds),
            "-m",
            str(max_time_seconds),
            "-H",
            f"User-Agent: {DEFAULT_USER_AGENT}",
            "-w",
            "\n|||%{url_effective}",
        ]

        bind = bind.strip()
        if bind:
            if IS_WINDOWS:
                if looks_like_bind_address(bind):
                    args.extend(["--interface", bind])
                else:
                    logging.debug(
                        "ignoring login_bind=%s on Windows; use a local IPv4 address if binding is needed",
                        bind,
                    )
            else:
                args.extend(["--interface", bind])

        if post_data is not None:
            args.extend(["-X", "POST", "-d", post_data])
        args.append(url)

        result = run_command(args, timeout=max_time_seconds + 2)
        if result.returncode != 0 and not result.stdout.strip():
            raise CommandError(f"curl failed with exit code {result.returncode}")

        output = result.stdout
        if not output.strip():
            raise CommandError("curl returned empty output")

        if "\n|||" in output:
            body, final_url = output.rsplit("\n|||", 1)
            return body, final_url.strip()
        return output, url

    def probe_portal(
        self,
        config: Config,
        *,
        connect_timeout_seconds: Optional[int] = None,
        max_time_seconds: Optional[int] = None,
    ) -> Tuple[str, str, Optional[str]]:
        try:
            body, final_url = self.run_curl(
                config.probe_url,
                bind=config.login_bind,
                connect_timeout_seconds=connect_timeout_seconds
                or config.connect_timeout_seconds,
                max_time_seconds=max_time_seconds or config.curl_max_time_seconds,
                post_data=None,
            )
        except CommandError as exc:
            return "offline", str(exc), None

        if any(marker in final_url for marker in SUCCESS_URL_MARKERS):
            return "online", "portal confirms already online", None

        query_string = extract_query_string(body)
        if query_string:
            return "login_required", "SCUNET portal requires login", query_string

        return "non_portal", "SCUNET portal was not detected", None

    def should_attempt_login(self, config: Config) -> Tuple[bool, str]:
        if not self.interface_is_active(config.detect_interface):
            return False, f"interface {config.detect_interface} is not active"

        if self.is_wifi_interface(config.detect_interface):
            ssid = self.current_ssid(config.detect_interface)
            if not config.target_ssid:
                return True, "connected to a Wi-Fi network"
            if ssid == config.target_ssid:
                return True, f"connected to target SSID {ssid}"
            if ssid is None:
                portal_state, portal_reason, _ = self.probe_portal(
                    config,
                    connect_timeout_seconds=1,
                    max_time_seconds=2,
                )
                if portal_state in {"online", "login_required"}:
                    return True, "SSID unavailable, but SCUNET portal was detected"
                if config.allow_unknown_wifi_name:
                    return True, "Wi-Fi name unavailable, but unknown Wi-Fi is allowed"
                return False, f"SSID unavailable and {portal_reason.lower()}"
            return False, f"current SSID does not match target ({ssid or 'unknown'})"

        if config.target_ssid:
            return False, f"interface {config.detect_interface} is not a Wi-Fi adapter"

        return True, f"active non-Wi-Fi interface {config.detect_interface}"

    def login(
        self,
        config: Config,
        *,
        precomputed_query_string: Optional[str] = None,
    ) -> Tuple[bool, str]:
        password = read_password(config)
        if not password:
            return False, "no password available from the configured password source"

        query_string = precomputed_query_string
        if not query_string:
            probe_state, message, query_string = self.probe_portal(config)
            if probe_state == "online":
                return True, message
            if probe_state != "login_required" or not query_string:
                return False, "unable to extract queryString from the portal page"

        post_body = build_post_body(config, password, query_string)
        login_body, _ = self.run_curl(
            f"{config.probe_url.rstrip('/')}/eportal/InterFace.do?method=login",
            bind=config.login_bind,
            connect_timeout_seconds=config.connect_timeout_seconds,
            max_time_seconds=config.curl_max_time_seconds,
            post_data=post_body,
        )

        if '"result":"success"' in login_body:
            return True, "login succeeded"

        error_message = extract_error_message(login_body) or "unknown portal error"
        return False, error_message

    def run_cycle(self, *, dry_run: bool = False) -> int:
        config = load_config(self.config_path)

        if not config.auto_login_enabled:
            self.set_state("auto login is disabled")
            return config.idle_interval_seconds

        should_attempt, reason = self.should_attempt_login(config)
        if not should_attempt:
            self.set_state(reason)
            return config.idle_interval_seconds

        portal_state, portal_message, query_string = self.probe_portal(config)
        if portal_state == "online":
            self.set_state(portal_message)
            return config.poll_interval_seconds
        if portal_state == "non_portal":
            self.set_state(portal_message)
            return config.poll_interval_seconds
        if portal_state == "offline":
            self.set_state(f"portal probe failed: {portal_message}")
            return config.poll_interval_seconds

        self.set_state(reason)

        gap = time.monotonic() - self.last_login_attempt_at
        if gap < config.min_login_interval_seconds:
            remaining = int(config.min_login_interval_seconds - gap)
            self.set_state(f"waiting for login cooldown ({remaining}s remaining)")
            return config.poll_interval_seconds

        if dry_run:
            self.set_state("dry-run: login would be triggered now")
            return config.poll_interval_seconds

        self.last_login_attempt_at = time.monotonic()
        logging.info("portal probe requires login, trying portal login")

        try:
            success, message = self.login(
                config,
                precomputed_query_string=query_string,
            )
        except (CommandError, ConfigError) as exc:
            self.set_state(f"login command failed: {exc}")
            return config.poll_interval_seconds

        if success:
            self.set_state(message)
        else:
            self.set_state(f"login failed: {message}")
        return config.poll_interval_seconds

    def run_once(self, *, dry_run: bool = False) -> int:
        try:
            self.run_cycle(dry_run=dry_run)
        except (CommandError, ConfigError) as exc:
            logging.error("%s", exc)
            return 1
        return 0

    def run_forever(self, *, dry_run: bool = False) -> int:
        self.install_signal_handlers()
        while not self.stop_requested:
            try:
                sleep_seconds = self.run_cycle(dry_run=dry_run)
            except (CommandError, ConfigError) as exc:
                logging.error("%s", exc)
                sleep_seconds = 10
            except Exception:
                logging.exception("unexpected error")
                sleep_seconds = 10
            self.sleep_interruptibly(sleep_seconds)
        return 0


def parse_args() -> argparse.Namespace:
    default_config = Path(__file__).resolve().with_name(DEFAULT_CONFIG_NAME)
    parser = argparse.ArgumentParser(
        description="Cross-platform SCUNET auto-login daemon."
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=default_config,
        help=f"path to config JSON (default: {default_config})",
    )
    parser.add_argument(
        "--once",
        action="store_true",
        help="run one probe/login cycle and exit",
    )
    parser.add_argument(
        "--check-config",
        action="store_true",
        help="validate the config file and exit",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="never perform login, only report what would happen",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="enable verbose logging",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    setup_logging(args.verbose)

    if args.check_config:
        try:
            config = load_config(args.config)
        except ConfigError as exc:
            logging.error("%s", exc)
            return 1
        logging.info(
            "config OK: platform=%s username=%s detect_interface=%s password_source=%s",
            SYSTEM,
            config.username,
            config.detect_interface,
            config.password_source,
        )
        return 0

    daemon = SCUNETAutologinDaemon(args.config)
    if args.once:
        return daemon.run_once(dry_run=args.dry_run)
    return daemon.run_forever(dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
