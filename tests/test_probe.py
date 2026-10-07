"""esptool の出力パース。実際に esptool を起動せず、標準出力の文面だけを与えて検証する。"""

from __future__ import annotations

import subprocess

import pytest

from porttag import probe as probemod

# esptool v5.1.0 + ESP32-C5 実機からそのまま採取した出力。
# 新しいチップは "MAC:" に EUI-64 を出し、実 MAC は "BASE MAC:" に出る。
V5_FLASH_ID = """esptool v5.1.0
Serial port /dev/cu.usbmodem211101:
Connecting...
Detecting chip type... ESP32-C5
Connected to ESP32-C5 on /dev/cu.usbmodem211101:
Chip type:          ESP32-C5 (revision v1.0)
Features:           Wi-Fi 6 (dual-band), BT 5 (LE), IEEE802.15.4, Single Core + LP Core, 240MHz
Crystal frequency:  48MHz
MAC:                3c:dc:75:ff:fe:00:00:01
BASE MAC:           3c:dc:75:00:00:01
MAC_EXT:            ff:fe

Uploading stub flasher...
Running stub flasher...
Stub flasher running.

Flash Memory Information:
=========================
Manufacturer: 46
Device: 4018
Detected flash size: 16MB

Hard resetting via RTS pin...
"""

#: 6 バイトの MAC だけを出す従来型（ESP32-S3 など）の v5 出力。
V5_FLASH_ID_PLAIN_MAC = """esptool v5.1.0
Connected to ESP32-S3 on /dev/cu.usbmodem212101:
Chip type:          ESP32-S3 (QFN56) (revision v0.1)
Features:           WiFi, BLE, Embedded PSRAM 8MB (AP_3v3)
Crystal frequency:  40MHz
USB mode:           USB-Serial/JTAG
MAC:                f4:12:fa:86:60:f8

Detected flash size: 16MB
Hard resetting via RTS pin...
"""

#: 他プロセスがポートを開いているときの実出力。エラー本文は stderr 側に出る。
PORT_BUSY_STDOUT = """esptool v5.1.0
Serial port /dev/cu.usbmodem2141401:
"""

PORT_BUSY_STDERR = """
A fatal error occurred: Could not open /dev/cu.usbmodem2141401, the port is busy or doesn't exist.
([Errno 35] Could not exclusively lock port /dev/cu.usbmodem2141401: [Errno 35] Resource temporarily unavailable)
"""

V4_FLASH_ID = """esptool.py v4.7.0
Serial port /dev/cu.usbserial-0001
Connecting....
Detecting chip type... ESP32
Chip is ESP32-D0WD-V3 (revision v3.1)
Features: WiFi, BT, Dual Core, 240MHz, VRef calibration in efuse, Coding Scheme None
Crystal is 40MHz
MAC: 24:0a:c4:11:22:33
Uploading stub...
Running stub...
Stub running...
Manufacturer: c8
Device: 4016
Detected flash size: 4MB
Hard resetting via RTS pin...
"""

CONNECT_FAILURE = """esptool v5.1.0
Connecting.....................................

A fatal error occurred: Failed to connect to Espressif device: No serial data received.
"""


class FakeCompleted:
    def __init__(self, stdout="", stderr="", returncode=0):
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode


@pytest.fixture
def fake_run(monkeypatch):
    """subprocess.run を差し替え、渡された argv を記録する。"""
    calls = []

    def install(result):
        def runner(argv, **kwargs):
            calls.append(argv)
            if argv[-1] == "version":
                return FakeCompleted(stdout="esptool v5.1.0\n")
            if isinstance(result, Exception):
                raise result
            return result

        monkeypatch.setattr(probemod.subprocess, "run", runner)
        return calls

    return install


def test_parses_v5_output(fake_run):
    fake_run(FakeCompleted(stdout=V5_FLASH_ID))
    result = probemod.probe_port("/dev/cu.usbmodem211101", command=["esptool"], subcommand="flash-id")

    assert result.chip == "ESP32-C5"
    assert result.chip_description == "ESP32-C5 (revision v1.0)"
    assert result.features.startswith("Wi-Fi 6 (dual-band)")
    assert result.flash_size == "16MB"
    assert result.error is None
    # EUI-64 の先頭 6 バイト (3c:dc:75:ff:fe:00) ではなく BASE MAC を採ること。
    assert result.mac == "3c:dc:75:00:00:01"


def test_plain_six_byte_mac_is_still_parsed(fake_run):
    fake_run(FakeCompleted(stdout=V5_FLASH_ID_PLAIN_MAC))
    result = probemod.probe_port("/dev/cu.usbmodem212101", command=["esptool"], subcommand="flash-id")

    assert result.chip == "ESP32-S3"
    assert result.mac == "f4:12:fa:86:60:f8"


def test_parses_v4_output(fake_run):
    fake_run(FakeCompleted(stdout=V4_FLASH_ID))
    result = probemod.probe_port("/dev/cu.usbserial-0001", command=["esptool.py"], subcommand="flash_id")

    assert result.chip == "ESP32"
    assert result.chip_description == "ESP32-D0WD-V3 (revision v3.1)"
    assert result.flash_size == "4MB"
    assert result.mac == "24:0a:c4:11:22:33"
    assert result.error is None


def test_connect_failure_is_summarized(fake_run):
    fake_run(FakeCompleted(stdout=CONNECT_FAILURE, returncode=2))
    result = probemod.probe_port("/dev/cu.usbmodem211101", command=["esptool"], subcommand="flash-id")

    assert result.chip is None
    assert "Failed to connect" in result.error
    assert "応答がありません" in result.error


def test_busy_port_error_is_not_mistaken_for_the_serial_port_banner(fake_run):
    """esptool は情報行として "Serial port <path>:" を出す。これを理由に採ってはいけない。"""
    fake_run(
        FakeCompleted(stdout=PORT_BUSY_STDOUT, stderr=PORT_BUSY_STDERR, returncode=2)
    )
    result = probemod.probe_port(
        "/dev/cu.usbmodem2141401", command=["esptool"], subcommand="flash-id"
    )

    assert result.chip is None
    assert result.error.startswith("ポートが使用中です")
    assert "the port is busy" in result.error
    assert result.error.strip() != "Serial port /dev/cu.usbmodem2141401:"


def test_timeout_is_reported_without_raising(fake_run):
    fake_run(subprocess.TimeoutExpired(cmd="esptool", timeout=5))
    result = probemod.probe_port(
        "/dev/cu.usbmodem211101", command=["esptool"], subcommand="flash-id", timeout=5
    )

    assert result.chip is None
    assert "タイムアウト" in result.error


def test_argv_includes_port_and_hard_reset(fake_run):
    calls = fake_run(FakeCompleted(stdout=V5_FLASH_ID))
    probemod.probe_port("/dev/cu.usbmodem211101", command=["esptool"], subcommand="flash-id")

    argv = calls[-1]
    assert "--port" in argv and "/dev/cu.usbmodem211101" in argv
    # 調べ終わったらファームウェアを走らせ直す。
    assert argv[argv.index("--after") + 1] == "hard-reset"
    assert argv[-1] == "flash-id"


def test_subcommand_name_follows_esptool_major_version(monkeypatch):
    monkeypatch.setattr(
        probemod.subprocess,
        "run",
        lambda argv, **kwargs: FakeCompleted(stdout="esptool.py v4.7.0\n"),
    )
    assert probemod._major_version(["esptool.py"]) == 4

    monkeypatch.setattr(
        probemod.subprocess,
        "run",
        lambda argv, **kwargs: FakeCompleted(stdout="esptool v5.1.0\n"),
    )
    assert probemod._major_version(["esptool"]) == 5


def test_missing_esptool_raises_with_install_hint(monkeypatch):
    monkeypatch.delenv(probemod.ESPTOOL_ENV, raising=False)
    monkeypatch.setattr(probemod.shutil, "which", lambda name: None)
    monkeypatch.setitem(__import__("sys").modules, "esptool", None)

    with pytest.raises(probemod.ProbeUnavailable, match="pipx"):
        probemod.find_esptool()


def test_env_override_takes_precedence(monkeypatch):
    monkeypatch.setenv(probemod.ESPTOOL_ENV, "/opt/py -m esptool")
    assert probemod.find_esptool() == ["/opt/py", "-m", "esptool"]


def test_legacy_env_override_is_still_accepted(monkeypatch):
    monkeypatch.delenv(probemod.ESPTOOL_ENV, raising=False)
    monkeypatch.setenv(probemod.LEGACY_ESPTOOL_ENV, "legacy-esptool")
    assert probemod.find_esptool() == ["legacy-esptool"]


def test_probe_devices_fills_mac_for_bridge_boards(fake_run, monkeypatch):
    from porttag.devices import Device

    monkeypatch.setenv(probemod.ESPTOOL_ENV, "esptool")
    fake_run(FakeCompleted(stdout=V4_FLASH_ID))
    dev = Device(port="/dev/cu.usbserial-0001", vid=0x10C4, pid=0xEA60, serial_number="0001")
    probemod.probe_devices([dev])

    assert dev.chip == "ESP32"
    assert dev.mac == "24:0A:C4:11:22:33"
    assert dev.flash_size == "4MB"
