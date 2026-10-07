from __future__ import annotations

import json

from porttag import aliases
from porttag.cli import build_parser, main

from conftest import BRIDGE_PORT, CH340_PORT, ESP32_PORTS, OTHER_PORT


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_top_level_help_is_self_discoverable():
    help_text = build_parser().format_help()

    assert "porttag name usbmodem211101 sensor-node" in help_text
    assert "porttag <command> --help" in help_text


def test_no_subcommand_lists_devices(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    code, out, _err = run([], capsys)

    assert code == 0
    assert "/dev/cu.usbmodem211101" in out
    assert "3C:DC:75:00:00:01" in out
    # 名前が付いていないことが一目で分かる。
    assert "(no name)" in out


def test_json_output_is_machine_readable(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    code, out, _err = run(["--json"], capsys)

    assert code == 0
    payload = json.loads(out)
    assert payload["count"] == 3
    assert payload["probed"] is False
    first = payload["devices"][0]
    assert first["port"] == "/dev/cu.usbmodem211101"
    assert first["mac"] == "3C:DC:75:00:00:01"
    assert first["identity_kind"] == "mac"
    assert first["alias"] is None


def test_name_then_list_shows_the_alias(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)

    code, out, _err = run(
        ["name", "usbmodem211101", "sensor-node", "--note", "居間の温湿度計"], capsys
    )
    assert code == 0
    assert "sensor-node" in out

    code, out, _err = run([], capsys)
    assert code == 0
    assert "sensor-node" in out
    assert "居間の温湿度計" in out


def test_port_subcommand_prints_only_the_path(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211201", "actuator"], capsys)

    code, out, err = run(["port", "actuator"], capsys)
    assert code == 0
    assert out.strip() == "/dev/cu.usbmodem211201"
    assert err == ""


def test_port_subcommand_fails_loudly_for_unknown_name(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    code, out, err = run(["port", "存在しない"], capsys)

    assert code == 1
    assert out == ""
    assert "一致する接続中のデバイスがありません" in err


def test_ambiguous_target_is_refused_rather_than_guessed(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    code, _out, err = run(["port", "usbmodem21"], capsys)

    assert code == 1
    assert "複数のデバイスに一致" in err


def test_duplicate_name_is_refused(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "node"], capsys)

    code, _out, err = run(["name", "usbmodem211201", "node"], capsys)
    assert code == 1
    assert "既に" in err


def test_unname_removes_the_alias(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node"], capsys)

    code, out, _err = run(["unname", "sensor-node"], capsys)
    assert code == 0
    assert "削除しました" in out

    _code, out, _err = run([], capsys)
    assert "sensor-node" not in out


def test_unname_works_for_a_disconnected_device(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node"], capsys)

    fake_ports([])  # 抜いた状態
    code, out, _err = run(["unname", "sensor-node"], capsys)
    assert code == 0
    assert "3C:DC:75:00:00:01" in out


def test_aliases_lists_disconnected_entries(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node"], capsys)

    fake_ports([])
    code, out, _err = run(["aliases"], capsys)
    assert code == 0
    assert "sensor-node" in out

    code, out, _err = run(["aliases", "--json"], capsys)
    payload = json.loads(out)
    assert payload["aliases"][0]["connected"] is False
    assert payload["aliases"][0]["port"] is None


def test_project_scope_is_used_when_a_project_file_exists(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    isolated_config["project"].write_text(
        json.dumps({"version": 1, "aliases": {}}), encoding="utf-8"
    )

    code, out, _err = run(["name", "usbmodem211101", "sensor-node"], capsys)
    assert code == 0
    assert aliases.PROJECT_FILENAME in out
    assert not isolated_config["global"].exists()


def test_global_flag_overrides_project_file(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    isolated_config["project"].write_text(
        json.dumps({"version": 1, "aliases": {}}), encoding="utf-8"
    )

    code, out, _err = run(["name", "usbmodem211101", "sensor-node", "--global"], capsys)
    assert code == 0
    assert isolated_config["global"].exists()


def test_unstable_identity_is_warned_when_naming(fake_ports, isolated_config, capsys):
    fake_ports([CH340_PORT])
    code, _out, err = run(["name", "wchusbserial110", "mystery-board"], capsys)

    assert code == 0
    assert "挿す場所を変えると" in err


def test_info_shows_details(fake_ports, isolated_config, capsys):
    fake_ports([BRIDGE_PORT])
    code, out, _err = run(["info", "usbserial-0001"], capsys)

    assert code == 0
    assert "CP2102" in out
    assert "serial:0001" in out


def test_plain_output_is_tab_separated(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node"], capsys)

    code, out, _err = run(["--plain"], capsys)
    assert code == 0
    first = out.splitlines()[0].split("\t")
    assert first == ["sensor-node", "/dev/cu.usbmodem211101", "3C:DC:75:00:00:01"]


def test_empty_device_list_is_reported_clearly(fake_ports, isolated_config, capsys):
    fake_ports([])
    code, out, _err = run([], capsys)

    assert code == 0
    assert "見つかりませんでした" in out


def test_list_orders_named_devices_by_name_then_unnamed_by_location(
    fake_ports, isolated_config, capsys
):
    fake_ports(ESP32_PORTS)
    # 物理位置順は 211101 → 211201 → 2141401。名前順はこれと逆になるよう付ける。
    run(["name", "usbmodem211201", "Core_0A10"], capsys)
    run(["name", "usbmodem2141401", "core_0900"], capsys)

    code, out, _err = run(["--json"], capsys)
    assert code == 0
    ports = [d["port"] for d in json.loads(out)["devices"]]
    assert ports == [
        "/dev/cu.usbmodem2141401",  # core_0900 (固定桁 16 進は文字列順で値の順、大小文字は無視)
        "/dev/cu.usbmodem211201",  # Core_0A10
        "/dev/cu.usbmodem211101",  # 名前なしは末尾
    ]


def test_list_aliases_and_link_share_the_same_name_order(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    # 大小文字を区別する順だと "Beta" が "alpha" より前に来てしまう。
    run(["name", "usbmodem211101", "Beta", "--global"], capsys)
    run(["name", "usbmodem211201", "alpha", "--global"], capsys)
    expected = ["alpha", "Beta"]

    _code, out, _err = run(["--json"], capsys)
    assert [d["alias"] for d in json.loads(out)["devices"]][:2] == expected

    _code, out, _err = run(["aliases", "--json"], capsys)
    assert [e["name"] for e in json.loads(out)["aliases"]] == expected

    _code, out, _err = run(["link"], capsys)
    linked = [line.split(" -> ")[0].strip() for line in out.splitlines() if " -> " in line]
    assert linked == expected


def test_list_shows_detected_count(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS + [OTHER_PORT])
    code, out, _err = run([], capsys)
    assert code == 0
    assert "3 台を検出しました。" in out

    code, out, _err = run(["--all"], capsys)
    assert code == 0
    assert "4 ポートを検出しました（うち ESP32 系 3 台）。" in out


def test_tree_groups_devices_by_hub_path(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS + [OTHER_PORT])
    run(["name", "usbmodem2141401", "deep-node"], capsys)

    code, out, _err = run(["--tree", "--all"], capsys)
    assert code == 0
    lines = out.splitlines()
    assert lines[0] == "バス 2"
    # 2-1.1.1 と 2-1.1.2 は同じハブ 1.1 の下、2-1.4.1.4 は別経路 (単独の鎖は 1 行に畳む)。
    assert any(l.startswith("└ 1  ハブ") for l in lines)
    assert any(l.startswith("  ├ 1  ハブ") for l in lines)
    assert any(l.startswith("  │ ├ 1 ") and "usbmodem211101" in l for l in lines)
    assert any(l.startswith("  │ └ 2 ") and "usbmodem211201" in l for l in lines)
    assert any(l.startswith("  └ 4.1.4 ") and "deep-node" in l for l in lines)
    # location の無いポートは末尾にまとめる。
    assert "経路不明" in out
    assert out.index("経路不明") < out.index(OTHER_PORT.device)
    assert "4 ポートを検出しました（うち ESP32 系 3 台）。" in out


def test_tree_accepts_linux_style_location():
    from porttag.render import _parse_location

    assert _parse_location("1-1.4:1.0") == ("1", (1, 4))
    assert _parse_location("2-1.1.4.3") == ("2", (1, 1, 4, 3))
    assert _parse_location(None) is None
    assert _parse_location("garbage") is None


def test_port_prefix_is_omitted_only_in_human_views(fake_ports, isolated_config, capsys, monkeypatch):
    from porttag import render

    monkeypatch.setattr(render.sys, "platform", "darwin")
    fake_ports(ESP32_PORTS)

    _code, out, _err = run([], capsys)
    assert "PORT (/dev/cu. 省略)" in out
    rows = [l for l in out.splitlines() if "3C:DC:75:00:00:01" in l]
    assert rows and "usbmodem211101" in rows[0] and "/dev/cu." not in rows[0]

    _code, out, _err = run(["--tree"], capsys)
    assert "ポートは /dev/cu. を省略して表示しています。" in out
    rows = [l for l in out.splitlines() if "3C:DC:75:00:00:01" in l]
    assert rows and "/dev/cu." not in rows[0]

    # 機械向けの出力とパス取得は完全パスのまま。
    _code, out, _err = run(["--plain"], capsys)
    assert "/dev/cu.usbmodem211101" in out
    _code, out, _err = run(["port", "usbmodem211101"], capsys)
    assert out.strip() == "/dev/cu.usbmodem211101"


def test_port_prefix_depends_on_platform_and_all_ports():
    from porttag import devices as devmod, render

    mac = [devmod.Device(port="/dev/cu.usbmodem1"), devmod.Device(port="/dev/cu.usbserial-1")]
    assert render.port_prefix(mac, platform="darwin") == "/dev/cu."
    assert render.port_prefix(mac, platform="win32") == ""
    linux = [devmod.Device(port="/dev/ttyUSB0"), devmod.Device(port="/dev/ttyACM0")]
    assert render.port_prefix(linux, platform="linux") == "/dev/"
    mixed = mac + [devmod.Device(port="/dev/tty.usbmodem1")]
    assert render.port_prefix(mixed, platform="darwin") == ""
    assert render.port_prefix([], platform="darwin") == ""
