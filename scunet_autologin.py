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
        "curl",
    ]
)
SECURITY_BIN = resolve_command(
    ["/usr/bin/security", "security"]
)
OSASCRIPT_BIN = resolve_command(
    ["/usr/bin/osascript", "osascript"]
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
        return shlex.split(value, posix=True)
    raise ConfigError("password_command must be a JSON string or array")


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
    detect_interface: str = "PUT_INTERFACE_NAME_HERE"
    login_bind: str = ""
    probe_url: str = "http://192.168.2.135/"
    auto_login_enabled: bool = True
    poll_interval_seconds: int = 1
    idle_interval_seconds: int = 1
    preflight_connect_timeout_seconds: int = 2
    preflight_max_time_seconds: int = 3
    connect_timeout_seconds: int = 3
    curl_max_time_seconds: int = 5
    min_login_interval_seconds: int = 5
    notifications_enabled: bool = True
    notify_on_success: bool = True
    notify_on_attempt: bool = True
    notification_min_interval_seconds: int = 60
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

        if normalized.get("login_interface") and not normalized.get("login_bind"):
            normalized["login_bind"] = normalized["login_interface"]
        if "password_source" not in normalized:
            normalized["password_source"] = "macos_keychain"

        dataclass_keys = {field.name for field in fields(cls)}
        clean = {key: normalized[key] for key in normalized if key in dataclass_keys}
        config = cls(**clean)

        if not config.login_bind:
            config.login_bind = config.detect_interface
        return config

    def validate(self) -> None:
        if not IS_MACOS:
            raise ConfigError("this daemon only supports macOS")
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
        if not self.probe_url:
            raise ConfigError("config.probe_url is required")
        if self.poll_interval_seconds <= 0:
            raise ConfigError("config.poll_interval_seconds must be > 0")
        if self.idle_interval_seconds <= 0:
            raise ConfigError("config.idle_interval_seconds must be > 0")
        if self.preflight_connect_timeout_seconds <= 0:
            raise ConfigError("config.preflight_connect_timeout_seconds must be > 0")
        if self.preflight_max_time_seconds <= 0:
            raise ConfigError("config.preflight_max_time_seconds must be > 0")
        if self.min_login_interval_seconds < 0:
            raise ConfigError("config.min_login_interval_seconds must be >= 0")
        if self.notification_min_interval_seconds < 0:
            raise ConfigError("config.notification_min_interval_seconds must be >= 0")
        if self.password_source not in PASSWORD_SOURCES:
            raise ConfigError(
                f"config.password_source must be one of: {', '.join(sorted(PASSWORD_SOURCES))}"
            )
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
    return expand_path("~/Library/Logs/SCUNETAutologin/daemon.log")


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
        self.last_notification_at: Dict[str, float] = {}

    def install_signal_handlers(self) -> None:
        signal.signal(signal.SIGINT, self._handle_stop)
        signal.signal(signal.SIGTERM, self._handle_stop)

    def _handle_stop(self, signum: int, _frame) -> None:
        logging.info("received signal %s, stopping", signum)
        self.stop_requested = True

    def set_state(self, state: str) -> bool:
        if state != self.last_state:
            logging.info("%s", state)
            self.last_state = state
            return True
        return False

    def update_state(self, state: str, config: Optional[Config] = None) -> None:
        changed = self.set_state(state)
        if changed and config is not None:
            self.maybe_notify_state(config, state)

    def maybe_notify_state(self, config: Config, state: str) -> None:
        if not IS_MACOS or not config.notifications_enabled:
            return

        notification_key = None
        subtitle = None
        body = None

        context = self.notification_context(config)

        if state == "login succeeded" and config.notify_on_success:
            notification_key = "login_succeeded"
            subtitle = "认证成功"
            body = f"SCUNET 已完成自动认证。\n{context}"
        elif state.startswith("login failed: "):
            notification_key, subtitle, body = self.classify_login_failed_notification(
                state.removeprefix("login failed: "),
                context,
            )
        elif state.startswith("login command failed: "):
            notification_key, subtitle, body = self.classify_command_failure_notification(
                "login_command_failed",
                state.removeprefix("login command failed: "),
                context,
            )
        elif state.startswith("portal probe failed: "):
            notification_key, subtitle, body = self.classify_command_failure_notification(
                "portal_probe_failed",
                state.removeprefix("portal probe failed: "),
                context,
            )
        elif state.startswith("interface ") and " is not active" in state:
            notification_key = "interface_inactive"
            subtitle = "网卡未连接"
            body = f"{state}\n{context}\n建议: 检查 Wi‑Fi 是否已连接到 SCUNET。"

        if not notification_key or not body or not subtitle:
            return

        now = time.monotonic()
        last_at = self.last_notification_at.get(notification_key, 0.0)
        if now - last_at < config.notification_min_interval_seconds:
            return

        self.last_notification_at[notification_key] = now
        self.send_macos_notification(subtitle, body)

    def maybe_notify_login_attempt(self, config: Config) -> None:
        if not IS_MACOS or not config.notifications_enabled or not config.notify_on_attempt:
            return

        notification_key = "login_attempt"
        now = time.monotonic()
        last_at = self.last_notification_at.get(notification_key, 0.0)
        if now - last_at < config.notification_min_interval_seconds:
            return

        self.last_notification_at[notification_key] = now
        self.send_macos_notification(
            "开始认证",
            f"检测到需要进行 SCUNET 认证。\n{self.notification_context(config)}",
        )

    def classify_login_failed_notification(
        self,
        message: str,
        context: str,
    ) -> Tuple[str, str, str]:
        lower_message = message.lower()

        if "用户不存在" in message or "username" in lower_message and "exist" in lower_message:
            return (
                "login_failed_username",
                "用户名错误",
                f"{message}\n{context}\n建议: 检查学号/用户名是否填写正确。",
            )

        if "密码" in message and ("错误" in message or "不对" in message):
            return (
                "login_failed_password",
                "密码错误",
                f"{message}\n{context}\n建议: 重新保存校园网密码后再试。",
            )

        if "服务" in message or "运营商" in message:
            return (
                "login_failed_service",
                "运营商可能不对",
                f"{message}\n{context}\n建议: 检查当前是否应使用 EDUNET / 电信 / 移动 / 联通。",
            )

        return (
            "login_failed",
            "认证失败",
            f"{message}\n{context}",
        )

    def classify_command_failure_notification(
        self,
        key_prefix: str,
        message: str,
        context: str,
    ) -> Tuple[str, str, str]:
        lower_message = message.lower()

        if "timed out" in lower_message:
            return (
                f"{key_prefix}_timeout",
                "请求超时",
                f"{message}\n{context}\n建议: 当前网络响应较慢，稍后会自动重试。",
            )

        if "curl failed" in lower_message:
            return (
                f"{key_prefix}_curl_failed",
                "请求发送失败",
                f"{message}\n{context}\n建议: 检查 Wi‑Fi 状态和门户地址是否可达。",
            )

        return (
            key_prefix,
            "认证异常" if key_prefix == "login_command_failed" else "网络探测异常",
            f"{message}\n{context}",
        )

    def notification_context(self, config: Config) -> str:
        return (
            f"账号: {config.username}\n"
            f"运营商: {config.service}\n"
            f"网卡: {config.detect_interface}"
        )

    def send_macos_notification(self, subtitle: str, body: str) -> None:
        def escape_apple_script(value: str) -> str:
            return value.replace("\\", "\\\\").replace('"', '\\"')

        script = (
            f'display notification "{escape_apple_script(body)}" '
            f'with title "SCUNET 自动认证" '
            f'subtitle "{escape_apple_script(subtitle)}"'
        )
        try:
            run_command([OSASCRIPT_BIN, "-e", script], timeout=5)
        except CommandError:
            logging.debug("failed to send macOS notification")

    def sleep_interruptibly(self, seconds: int) -> None:
        deadline = time.monotonic() + seconds
        while not self.stop_requested and time.monotonic() < deadline:
            time.sleep(min(1.0, max(0.1, deadline - time.monotonic())))

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
            self.update_state("auto login is disabled", config)
            return config.idle_interval_seconds

        portal_state, portal_message, query_string = self.probe_portal(config)
        if portal_state == "online":
            self.update_state(portal_message, config)
            return config.poll_interval_seconds
        if portal_state == "non_portal":
            self.update_state(portal_message, config)
            return config.poll_interval_seconds
        if portal_state == "offline":
            self.update_state(f"portal probe failed: {portal_message}", config)
            return config.poll_interval_seconds

        self.update_state("SCUNET portal detected", config)

        gap = time.monotonic() - self.last_login_attempt_at
        if gap < config.min_login_interval_seconds:
            remaining = int(config.min_login_interval_seconds - gap)
            self.update_state(f"waiting for login cooldown ({remaining}s remaining)", config)
            return config.poll_interval_seconds

        if dry_run:
            self.update_state("dry-run: login would be triggered now", config)
            return config.poll_interval_seconds

        self.last_login_attempt_at = time.monotonic()
        logging.info("portal probe requires login, trying portal login")
        self.maybe_notify_login_attempt(config)

        try:
            success, message = self.login(
                config,
                precomputed_query_string=query_string,
            )
        except (CommandError, ConfigError) as exc:
            self.update_state(f"login command failed: {exc}", config)
            return config.poll_interval_seconds

        if success:
            self.update_state(message, config)
        else:
            self.update_state(f"login failed: {message}", config)
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
        description="macOS SCUNET auto-login daemon."
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
    if not IS_MACOS:
        print("SCUNET autologin now only supports macOS.", file=sys.stderr)
        return 1
    setup_logging(args.verbose)

    if args.check_config:
        try:
            config = load_config(args.config)
        except ConfigError as exc:
            logging.error("%s", exc)
            return 1
        logging.info(
            "config OK: username=%s detect_interface=%s password_source=%s",
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
