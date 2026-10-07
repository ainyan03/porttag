"""シンボリックリンク置き場（porttag link）のテスト。"""

from __future__ import annotations

import os
from dataclasses import replace

from porttag.cli import main

from conftest import ESP32_PORTS


def run(argv, capsys):
    code = main(argv)
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_global_link_created_on_any_command(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node", "--global"], capsys)

    link = isolated_config["global_link_dir"] / "sensor-node"
    assert link.is_symlink()
    assert os.readlink(link) == "/dev/cu.usbmodem211101"


def test_link_retargets_after_port_change(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node", "--global"], capsys)

    # 同じ個体（同じシリアル番号）が別のポート名で再列挙された状態。
    moved = [replace(ESP32_PORTS[0], device="/dev/cu.usbmodem999901")] + ESP32_PORTS[1:]
    fake_ports(moved)
    run([], capsys)

    link = isolated_config["global_link_dir"] / "sensor-node"
    assert os.readlink(link) == "/dev/cu.usbmodem999901"


def test_link_removed_when_device_disconnected(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node", "--global"], capsys)
    link = isolated_config["global_link_dir"] / "sensor-node"
    assert link.is_symlink()

    fake_ports(ESP32_PORTS[1:])
    run([], capsys)
    assert not link.is_symlink()


def test_link_removed_after_unname(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node", "--global"], capsys)
    link = isolated_config["global_link_dir"] / "sensor-node"
    assert link.is_symlink()

    run(["unname", "sensor-node"], capsys)
    assert not link.is_symlink()


def test_project_link_dir_requires_explicit_link_command(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node", "--project"], capsys)

    # name しただけではプロジェクト側のディレクトリは作られない。
    assert not isolated_config["project_link_dir"].exists()

    code, out, _err = run(["link"], capsys)
    assert code == 0
    link = isolated_config["project_link_dir"] / "sensor-node"
    assert link.is_symlink()
    assert os.readlink(link) == "/dev/cu.usbmodem211101"
    assert ".gitignore" in out  # 初回作成時のヒント

    # 一度作られた後は、他のコマンドでも同期が維持される。
    fake_ports(ESP32_PORTS[1:])
    run([], capsys)
    assert not link.is_symlink()


def test_project_farm_includes_global_names(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node", "--project"], capsys)
    run(["name", "usbmodem211201", "display-unit", "--global"], capsys)

    run(["link"], capsys)
    # プロジェクト側の置き場はマージ後の視点（グローバル名も引ける）。
    assert (isolated_config["project_link_dir"] / "sensor-node").is_symlink()
    assert (isolated_config["project_link_dir"] / "display-unit").is_symlink()
    # グローバル側にはグローバル登録の名前だけが並ぶ。
    assert (isolated_config["global_link_dir"] / "display-unit").is_symlink()
    assert not (isolated_config["global_link_dir"] / "sensor-node").exists()


def test_link_lists_farms(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    run(["name", "usbmodem211101", "sensor-node", "--global"], capsys)

    code, out, _err = run(["link"], capsys)
    assert code == 0
    assert "sensor-node -> /dev/cu.usbmodem211101" in out
    assert str(isolated_config["global_link_dir"]) in out


def test_unsafe_name_is_rejected(fake_ports, isolated_config, capsys):
    fake_ports(ESP32_PORTS)
    code, _out, err = run(["name", "usbmodem211101", "../evil", "--global"], capsys)
    assert code != 0
    assert "使えません" in err
    assert not isolated_config["global"].exists()


def test_regular_file_in_farm_is_left_alone(fake_ports, isolated_config, capsys):
    farm = isolated_config["global_link_dir"]
    farm.mkdir(parents=True)
    keep = farm / "sensor-node"
    keep.write_text("user data")

    fake_ports(ESP32_PORTS)
    _code, _out, err = run(["name", "usbmodem211101", "sensor-node", "--global"], capsys)
    assert keep.read_text() == "user data"
    assert "触りません" in err
