"""USB シリアルポートの列挙と ESP32 系デバイスの判定."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Optional

from serial.tools import list_ports

#: Espressif 純正の USB Vendor ID。これに一致すればチップ内蔵 USB であり ESP32 系で確定する。
ESPRESSIF_VID = 0x303A

#: Espressif 純正 VID のデバイスが持つ PID から読み取れる接続形態。
#: PID はチップ種別ごとには分かれていないため、種別の確定には使えない。
ESPRESSIF_PID_HINTS: dict[int, str] = {
    0x0002: "native USB CDC (ESP32-S2)",
    0x0009: "native USB CDC (ESP32-S3)",
    0x1001: "USB-Serial/JTAG",
    0x4001: "Espressif USB device",
    0x4002: "DFU",
}

#: ESP32 ボードに載る代表的な USB-UART ブリッジ。ブリッジ越しでは MAC もチップ種別も
#: 非破壊では取得できないため、ESP32 かどうかは「候補」に留まる。
BRIDGE_VIDS: dict[int, str] = {
    0x10C4: "Silicon Labs CP210x",
    0x1A86: "WCH CH34x",
    0x0403: "FTDI",
    0x067B: "Prolific PL2303",
}

# 識別の確からしさ。取り違え防止が目的なので、弱い識別子は弱いと明示する。
KIND_ESPRESSIF = "espressif"  # Espressif VID 一致 = ESP32 系で確定
KIND_BRIDGE = "bridge"  # USB-UART ブリッジ = ESP32 かもしれない
KIND_OTHER = "other"  # それ以外のシリアルポート

_MAC_RE = re.compile(r"^([0-9A-Fa-f]{2}[:-]){5}[0-9A-Fa-f]{2}$")
_NUM_RE = re.compile(r"(\d+)")


def normalize_mac(value: Optional[str]) -> Optional[str]:
    """MAC アドレス風の文字列を ``AA:BB:CC:DD:EE:FF`` 形式へ正規化する。"""
    if not value:
        return None
    text = value.strip()
    if not _MAC_RE.match(text):
        return None
    return text.replace("-", ":").upper()


@dataclass
class Device:
    """一覧に載せる 1 台ぶんの情報。"""

    port: str
    vid: Optional[int] = None
    pid: Optional[int] = None
    manufacturer: Optional[str] = None
    product: Optional[str] = None
    serial_number: Optional[str] = None
    location: Optional[str] = None

    # 導出情報
    kind: str = KIND_OTHER
    mac: Optional[str] = None
    interface_hint: Optional[str] = None

    # エイリアス（aliases モジュールが後から埋める）
    alias: Optional[str] = None
    note: Optional[str] = None
    alias_scope: Optional[str] = None
    #: 実際に名前が登録されているキー。identity より弱いキーのこともある。
    alias_key: Optional[str] = None

    # --probe の結果（probe モジュールが後から埋める）
    chip: Optional[str] = None
    chip_description: Optional[str] = None
    features: Optional[str] = None
    flash_size: Optional[str] = None
    probe_error: Optional[str] = None

    @property
    def identity_candidates(self) -> list[str]:
        """このデバイスを指しうるキーを、強い順に並べたもの。

        ``--probe`` するとブリッジ越しのボードでも MAC が判明し、identity が
        ``serial:...`` から MAC へ格上げされる。過去に弱いキーで登録した名前を
        引けなくしないため、参照時は候補すべてを順に試す。
        """
        keys: list[str] = []
        if self.mac:
            keys.append(self.mac)
        if self.serial_number:
            keys.append(f"serial:{self.serial_number}")
        if self.location:
            keys.append(f"location:{self.location}")
        keys.append(f"port:{self.port}")
        return keys

    @property
    def identity(self) -> str:
        """エイリアスを結びつける既定のキー。候補のうち最も強いもの。"""
        return self.identity_candidates[0]

    @property
    def identity_kind(self) -> str:
        """identity が何に由来するか。表示側が確からしさを伝えるために使う。"""
        if self.mac:
            return "mac"
        if self.serial_number:
            return "usb-serial"
        if self.location:
            return "usb-location"
        return "port-path"

    @property
    def is_stable_identity(self) -> bool:
        """個体に紐づく識別子か。False なら挿し替えで別デバイスを指しうる。"""
        return self.identity_kind in ("mac", "usb-serial")

    @property
    def usb_id(self) -> Optional[str]:
        if self.vid is None or self.pid is None:
            return None
        return f"{self.vid:04x}:{self.pid:04x}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "identity": self.identity,
            "identity_kind": self.identity_kind,
            "stable_identity": self.is_stable_identity,
            "alias": self.alias,
            "note": self.note,
            "alias_scope": self.alias_scope,
            "alias_key": self.alias_key,
            "port": self.port,
            "kind": self.kind,
            "mac": self.mac,
            "usb_id": self.usb_id,
            "vid": self.vid,
            "pid": self.pid,
            "manufacturer": self.manufacturer,
            "product": self.product,
            "serial_number": self.serial_number,
            "location": self.location,
            "interface_hint": self.interface_hint,
            "chip": self.chip,
            "chip_description": self.chip_description,
            "features": self.features,
            "flash_size": self.flash_size,
            "probe_error": self.probe_error,
        }


def _classify(dev: Device) -> None:
    """VID/PID から種別・MAC・接続形態を埋める。"""
    if dev.vid == ESPRESSIF_VID:
        dev.kind = KIND_ESPRESSIF
        dev.interface_hint = ESPRESSIF_PID_HINTS.get(dev.pid or -1)
        # チップ内蔵 USB の場合、USB シリアル番号にそのチップの MAC が入る。
        dev.mac = normalize_mac(dev.serial_number)
    elif dev.vid in BRIDGE_VIDS:
        dev.kind = KIND_BRIDGE
        dev.interface_hint = BRIDGE_VIDS[dev.vid]
    else:
        dev.kind = KIND_OTHER


def _natural_key(text: str) -> tuple:
    """数字部分を数値として比べるキー。"2-1.1.10" や "node10" を "…2" より後ろに置く。"""
    parts: list[Any] = []
    for token in _NUM_RE.split(text):
        if not token:
            continue
        parts.append((0, int(token), "") if token.isdigit() else (1, 0, token))
    return tuple(parts)


def _sort_key(dev: Device) -> tuple:
    """USB の物理位置順。抜き差ししても並びが動きにくい順序にする。"""
    src = dev.location or dev.port
    return (dev.kind != KIND_ESPRESSIF, dev.kind != KIND_BRIDGE, _natural_key(src), dev.port)


def name_sort_key(name: str) -> str:
    """名前を表示する一覧すべてで共有する並び順のキー (大小文字無視の文字列順)。

    数字を数値として比べる自然順にしないのは、"Core_0A10" のような固定桁の 16 進を
    含む名前で順序が崩れるため (自然順だと "0" と "a" と "10" に割れて "0900" より前に来る)。
    """
    return name.lower()


def sort_by_name(devices: list[Device]) -> None:
    """名前付きを名前順で先に、名前なしを後ろに並べ替える。

    安定ソートなので、名前なし同士は list_devices が付けた物理位置順のまま残る。
    """
    devices.sort(key=lambda d: (d.alias is None, name_sort_key(d.alias or "")))


def list_devices(include_all: bool = False) -> list[Device]:
    """接続中のシリアルポートを列挙する。

    既定では ESP32 系（Espressif VID）と、ESP32 ボードで使われる USB-UART ブリッジのみ返す。
    ``include_all`` を True にすると Bluetooth ポート等も含めた全ポートを返す。
    """
    devices: list[Device] = []
    for info in list_ports.comports():
        dev = Device(
            port=info.device,
            vid=info.vid,
            pid=info.pid,
            manufacturer=info.manufacturer,
            product=info.product,
            serial_number=info.serial_number,
            location=info.location,
        )
        _classify(dev)
        if not include_all and dev.kind == KIND_OTHER:
            continue
        devices.append(dev)
    devices.sort(key=_sort_key)
    return devices


def resolve(devices: list[Device], target: str) -> list[Device]:
    """ユーザーが打った文字列からデバイスを引く。

    エイリアス名 / MAC / identity / ポートのフルパス / ポート名の部分一致 を受け付ける。
    複数一致した場合は候補をすべて返し、呼び出し側で曖昧さを報告させる。
    """
    key = target.strip()
    lowered = key.lower()
    mac = normalize_mac(key)

    for matcher in (
        lambda d: d.alias is not None and d.alias == key,
        lambda d: d.alias is not None and d.alias.lower() == lowered,
        lambda d: mac is not None and d.mac == mac,
        lambda d: key in d.identity_candidates,
        lambda d: d.port == key,
        lambda d: d.port.lower().endswith(lowered),
        lambda d: lowered in d.port.lower(),
    ):
        hits = [d for d in devices if matcher(d)]
        if hits:
            return hits
    return []
