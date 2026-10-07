from __future__ import annotations

from porttag import devices as devmod
from porttag.devices import KIND_BRIDGE, KIND_ESPRESSIF

from conftest import BRIDGE_PORT, CH340_PORT, ESP32_PORTS, OTHER_PORT


def test_espressif_vid_is_detected_and_mac_comes_from_usb_serial(fake_ports):
    fake_ports(ESP32_PORTS)
    found = devmod.list_devices()

    assert len(found) == 3
    assert all(d.kind == KIND_ESPRESSIF for d in found)
    assert {d.mac for d in found} == {
        "3C:DC:75:00:00:01",
        "88:56:A6:00:00:02",
        "44:1B:F6:00:00:03",
    }
    assert all(d.identity_kind == "mac" for d in found)
    assert all(d.is_stable_identity for d in found)
    assert all(d.interface_hint == "USB-Serial/JTAG" for d in found)


def test_bridge_has_no_mac_but_keeps_usb_serial_as_identity(fake_ports):
    fake_ports([BRIDGE_PORT])
    (dev,) = devmod.list_devices()

    assert dev.kind == KIND_BRIDGE
    assert dev.mac is None
    assert dev.identity == "serial:0001"
    assert dev.identity_kind == "usb-serial"
    assert dev.is_stable_identity


def test_serialless_bridge_falls_back_to_usb_location_and_is_flagged_unstable(fake_ports):
    fake_ports([CH340_PORT])
    (dev,) = devmod.list_devices()

    assert dev.identity == "location:2-1.3"
    assert dev.identity_kind == "usb-location"
    assert not dev.is_stable_identity


def test_non_esp_ports_are_hidden_unless_all_is_requested(fake_ports):
    fake_ports([*ESP32_PORTS, OTHER_PORT])

    assert OTHER_PORT.device not in {d.port for d in devmod.list_devices()}
    assert OTHER_PORT.device in {d.port for d in devmod.list_devices(include_all=True)}


def test_ports_are_sorted_by_usb_location_not_by_string(fake_ports):
    # 文字列順なら "2-1.1.10" が "2-1.1.2" より前に来てしまう。
    ports = [
        type(ESP32_PORTS[0])(**{**ESP32_PORTS[0].__dict__, "location": "2-1.1.10"}),
        type(ESP32_PORTS[1])(**{**ESP32_PORTS[1].__dict__, "location": "2-1.1.2"}),
    ]
    fake_ports(ports)

    assert [d.location for d in devmod.list_devices()] == ["2-1.1.2", "2-1.1.10"]


def test_identity_candidates_are_ordered_strongest_first(fake_ports):
    fake_ports([BRIDGE_PORT])
    (dev,) = devmod.list_devices()

    assert dev.identity_candidates == [
        "serial:0001",
        "location:2-1.2",
        "port:/dev/cu.usbserial-0001",
    ]

    # probe で MAC が判明すると、より強いキーが先頭に増える（既存キーは残る）。
    dev.mac = "AA:BB:CC:DD:EE:FF"
    assert dev.identity_candidates[0] == "AA:BB:CC:DD:EE:FF"
    assert "serial:0001" in dev.identity_candidates


def test_resolve_accepts_alias_mac_port_and_suffix(fake_ports):
    fake_ports(ESP32_PORTS)
    found = devmod.list_devices()
    found[0].alias = "sensor-node"

    assert devmod.resolve(found, "sensor-node") == [found[0]]
    assert devmod.resolve(found, "SENSOR-NODE") == [found[0]]
    assert devmod.resolve(found, "3c:dc:75:00:00:01") == [found[0]]
    assert devmod.resolve(found, "/dev/cu.usbmodem211101") == [found[0]]
    assert devmod.resolve(found, "usbmodem211101") == [found[0]]
    assert devmod.resolve(found, "存在しない") == []


def test_resolve_reports_every_match_when_ambiguous(fake_ports):
    fake_ports(ESP32_PORTS)
    found = devmod.list_devices()

    # "usbmodem21" は 211101 / 211201 / 2141401 のいずれにも一致する。
    hits = devmod.resolve(found, "usbmodem21")
    assert len(hits) == 3

    # 一意に絞れる文字列なら 1 台だけ返る。
    assert len(devmod.resolve(found, "usbmodem2112")) == 1


def test_normalize_mac_accepts_hyphens_and_rejects_junk():
    assert devmod.normalize_mac("3c-dc-75-00-00-01") == "3C:DC:75:00:00:01"
    assert devmod.normalize_mac("3C:DC:75:00:00:01") == "3C:DC:75:00:00:01"
    assert devmod.normalize_mac("0001") is None
    assert devmod.normalize_mac(None) is None
