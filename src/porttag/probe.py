"""esptool を使ったチップ種別の深掘り取得。

USB 記述子だけではチップ種別（ESP32-S3 / C3 / C6 ...）は判別できない。確定させるには
esptool でブートローダに落として問い合わせる必要があり、その代償としてデバイスは
リセットされ、実行中のファームウェアは中断される。そのため既定では実行せず、
``--probe`` を指定したときだけこのモジュールが動く。
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from typing import Optional, Sequence

DEFAULT_TIMEOUT = 30.0
ESPTOOL_ENV = "PORTTAG_ESPTOOL"
LEGACY_ESPTOOL_ENV = "DEVUSB_NAME_ESPTOOL"


class ProbeUnavailable(Exception):
    """esptool が見つからない。CLI が導入方法を案内する。"""


@dataclass
class ProbeResult:
    chip: Optional[str] = None
    chip_description: Optional[str] = None
    features: Optional[str] = None
    flash_size: Optional[str] = None
    mac: Optional[str] = None
    error: Optional[str] = None


#: 6 バイトちょうどの MAC。直後にコロンが続く場合（EUI-64）は拾わない。
_MAC6 = r"([0-9a-fA-F]{2}(?::[0-9a-fA-F]{2}){5})(?![:0-9a-fA-F])"

_PATTERNS = {
    # v5: "Connected to ESP32-S3 on /dev/cu.xxx:"   v4: "Detecting chip type... ESP32-S3"
    "chip": (
        re.compile(r"Connected to (ESP[\w-]+) on", re.IGNORECASE),
        re.compile(r"Detecting chip type\.\.\.\s*(ESP[\w-]+)", re.IGNORECASE),
    ),
    # v5: "Chip type:  ESP32-S3 (QFN56) (revision v0.2)"   v4: "Chip is ESP32-S3 (revision v0.2)"
    "chip_description": (
        re.compile(r"Chip type:\s*(.+?)\s*$", re.MULTILINE),
        re.compile(r"Chip is\s+(.+?)\s*$", re.MULTILINE),
    ),
    "features": (re.compile(r"Features:\s*(.+?)\s*$", re.MULTILINE),),
    "flash_size": (re.compile(r"Detected flash size:\s*(\S+)", re.IGNORECASE),),
    # ESP32-C5 等の新しいチップは "MAC:" に EUI-64（8 バイト）を出し、6 バイトの実 MAC は
    # "BASE MAC:" に出す。先に BASE MAC を探し、"MAC:" 側は否定先読みで 6 バイトちょうどに限る
    # （そうしないと EUI-64 の先頭 6 バイト 3c:dc:75:ff:fe:00 を実 MAC と誤認する）。
    "mac": (
        re.compile(r"BASE MAC:\s*" + _MAC6),
        re.compile(r"MAC:\s*" + _MAC6),
    ),
}


def _first_match(text: str, patterns: Sequence[re.Pattern]) -> Optional[str]:
    for pattern in patterns:
        found = pattern.search(text)
        if found:
            return found.group(1).strip()
    return None


def find_esptool() -> list[str]:
    """esptool の起動コマンドを解決する。

    esptool は pipx や Homebrew で本ツールとは別の Python 環境に入っていることが多いため、
    import ではなく実行ファイルを探す。
    """
    override = os.environ.get(ESPTOOL_ENV) or os.environ.get(LEGACY_ESPTOOL_ENV)
    if override:
        return override.split()
    for name in ("esptool", "esptool.py"):
        found = shutil.which(name)
        if found:
            return [found]
    try:
        import esptool  # noqa: F401

        return [sys.executable, "-m", "esptool"]
    except ImportError:
        pass
    raise ProbeUnavailable(
        "esptool が見つかりません。'pipx install esptool' などで導入するか、"
        f"環境変数 {ESPTOOL_ENV} に実行コマンドを指定してください。"
    )


def _major_version(command: list[str]) -> int:
    """esptool のメジャーバージョン。サブコマンド名が v5 でケバブケースに変わったため必要。"""
    try:
        proc = subprocess.run(
            command + ["version"],
            capture_output=True,
            text=True,
            timeout=15,
            env=_child_env(),
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    found = re.search(r"v?(\d+)\.\d+", proc.stdout or proc.stderr or "")
    return int(found.group(1)) if found else 0


def _child_env() -> dict[str, str]:
    """esptool の装飾出力を抑えてパースしやすくする環境変数。"""
    env = dict(os.environ)
    env["NO_COLOR"] = "1"
    env["TERM"] = "dumb"
    env["COLUMNS"] = "200"
    env["PYTHONIOENCODING"] = "utf-8"
    return env


def probe_port(
    port: str,
    command: Optional[list[str]] = None,
    subcommand: Optional[str] = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> ProbeResult:
    """1 ポートを esptool で調べる。デバイスはリセットされる。"""
    if command is None:
        command = find_esptool()
    if subcommand is None:
        subcommand = "flash-id" if _major_version(command) >= 5 else "flash_id"

    argv = command + [
        "--port",
        port,
        "--connect-attempts",
        "2",
        "--after",
        "hard-reset",
        subcommand,
    ]
    try:
        proc = subprocess.run(
            argv, capture_output=True, text=True, timeout=timeout, env=_child_env()
        )
    except subprocess.TimeoutExpired:
        return ProbeResult(error=f"esptool がタイムアウトしました ({timeout:.0f}s)")
    except OSError as exc:
        return ProbeResult(error=f"esptool を実行できません: {exc}")

    text = (proc.stdout or "") + "\n" + (proc.stderr or "")
    result = ProbeResult(
        chip=_first_match(text, _PATTERNS["chip"]),
        chip_description=_first_match(text, _PATTERNS["chip_description"]),
        features=_first_match(text, _PATTERNS["features"]),
        flash_size=_first_match(text, _PATTERNS["flash_size"]),
        mac=_first_match(text, _PATTERNS["mac"]),
    )
    if proc.returncode != 0 and not result.chip:
        result.error = _summarize_failure(text) or f"esptool 失敗 (exit={proc.returncode})"
    return result


_FATAL_RE = re.compile(r"A fatal error occurred:\s*(.+)")
_ERROR_LINE_RE = re.compile(r"^(?:Error|Failed to|Could not|Timed out|Invalid)\b")


def _summarize_failure(text: str) -> Optional[str]:
    """esptool の失敗出力から理由を 1 行にまとめる。

    esptool は "Serial port <path>:" のような情報行も出すため、行頭から順に拾うと
    エラーでない行を掴んでしまう。まず致命エラー行を優先して探す。
    """
    found = _FATAL_RE.search(text)
    message: Optional[str] = None
    if found:
        message = found.group(1).strip()
    else:
        for line in text.splitlines():
            stripped = line.strip()
            if stripped and _ERROR_LINE_RE.match(stripped):
                message = stripped
                break
    if message is None:
        return None

    # 頻出する 2 つの原因は、esptool の英文だけでは対処が伝わりにくいので補足する。
    if "exclusively lock" in text or "port is busy" in text:
        return f"ポートが使用中です（シリアルモニタ等が開いている可能性）: {message}"
    if "No serial data received" in text:
        return f"応答がありません（ブートモードに入れない基板かもしれません）: {message}"
    return message


def probe_devices(devices, timeout: float = DEFAULT_TIMEOUT, max_workers: int = 4) -> None:
    """複数デバイスを並列に調べ、結果を各 Device へ書き戻す（破壊的更新）。

    esptool が無い場合は ProbeUnavailable を送出する。個々のポートの失敗は
    ``probe_error`` に格納され、他のデバイスの一覧表示は妨げない。
    """
    if not devices:
        return
    command = find_esptool()
    subcommand = "flash-id" if _major_version(command) >= 5 else "flash_id"

    def run(dev):
        return dev, probe_port(dev.port, command=command, subcommand=subcommand, timeout=timeout)

    workers = max(1, min(max_workers, len(devices)))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for dev, result in pool.map(run, devices):
            dev.chip = result.chip
            dev.chip_description = result.chip_description
            dev.features = result.features
            dev.flash_size = result.flash_size
            dev.probe_error = result.error
            # ブリッジ越しのボードは USB 記述子から MAC を取れないので、ここで初めて埋まる。
            if not dev.mac and result.mac:
                from .devices import normalize_mac

                dev.mac = normalize_mac(result.mac)
