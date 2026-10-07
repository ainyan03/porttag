"""実機なしでテストするための共通フィクスチャ。"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from porttag import aliases, devices as devmod  # noqa: E402


@dataclass
class FakePort:
    """serial.tools.list_ports.ListPortInfo の必要な属性だけを持つ替え玉。"""

    device: str
    vid: Optional[int] = None
    pid: Optional[int] = None
    manufacturer: Optional[str] = None
    product: Optional[str] = None
    serial_number: Optional[str] = None
    location: Optional[str] = None


#: 実機 4 台（ESP32 内蔵 USB-JTAG）から採取した値をもとにした一覧。
ESP32_PORTS = [
    FakePort(
        "/dev/cu.usbmodem211101",
        0x303A,
        0x1001,
        "Espressif",
        "USB JTAG/serial debug unit",
        "3C:DC:75:00:00:01",
        "2-1.1.1",
    ),
    FakePort(
        "/dev/cu.usbmodem211201",
        0x303A,
        0x1001,
        "Espressif",
        "USB JTAG/serial debug unit",
        "88:56:A6:00:00:02",
        "2-1.1.2",
    ),
    FakePort(
        "/dev/cu.usbmodem2141401",
        0x303A,
        0x1001,
        "Espressif",
        "USB JTAG/serial debug unit",
        "44:1B:F6:00:00:03",
        "2-1.4.1.4",
    ),
]

#: CP2102 ブリッジ経由のボード（MAC は非破壊では取れない）。
BRIDGE_PORT = FakePort(
    "/dev/cu.usbserial-0001",
    0x10C4,
    0xEA60,
    "Silicon Labs",
    "CP2102 USB to UART Bridge Controller",
    "0001",
    "2-1.2",
)

#: シリアル番号を持たない CH340（識別は USB の位置しかない）。
CH340_PORT = FakePort(
    "/dev/cu.wchusbserial110",
    0x1A86,
    0x7523,
    None,
    "USB Serial",
    None,
    "2-1.3",
)

#: ESP32 とは無関係のポート。既定では一覧に出さない。
OTHER_PORT = FakePort("/dev/cu.Bluetooth-Incoming-Port")


@pytest.fixture
def fake_ports(monkeypatch):
    """comports() の戻り値を差し替えるフィクスチャ。"""

    def install(ports):
        monkeypatch.setattr(devmod.list_ports, "comports", lambda: list(ports))
        return ports

    return install


@pytest.fixture
def isolated_config(monkeypatch, tmp_path):
    """エイリアスの保存先を tmp に閉じ込め、実ユーザーの設定を汚さないようにする。"""
    config_home = tmp_path / "config"
    state_home = tmp_path / "state"
    project_dir = tmp_path / "project"
    project_dir.mkdir(parents=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.setenv("XDG_STATE_HOME", str(state_home))
    monkeypatch.chdir(project_dir)
    return {
        "global": config_home / "porttag" / "aliases.json",
        "legacy_global": config_home / aliases.LEGACY_CONFIG_DIRNAME / "aliases.json",
        "project_dir": project_dir,
        "project": project_dir / aliases.PROJECT_FILENAME,
        "legacy_project": project_dir / aliases.LEGACY_PROJECT_FILENAME,
        "global_link_dir": state_home / "porttag" / "dev",
        "project_link_dir": project_dir / ".porttag" / "dev",
    }
