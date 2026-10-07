from __future__ import annotations

import json

import pytest

from porttag import aliases, devices as devmod

from conftest import BRIDGE_PORT, ESP32_PORTS


def test_set_and_lookup_roundtrip(isolated_config):
    store = aliases.AliasStore.load()
    path = store.set("3C:DC:75:00:00:01", "sensor-node", "居間の温湿度計", aliases.SCOPE_GLOBAL)

    assert path == isolated_config["global"]
    entry, scope = aliases.AliasStore.load().lookup("3C:DC:75:00:00:01")
    assert entry.name == "sensor-node"
    assert entry.note == "居間の温湿度計"
    assert scope == aliases.SCOPE_GLOBAL


def test_project_scope_wins_over_global(isolated_config):
    store = aliases.AliasStore.load()
    store.set("3C:DC:75:00:00:01", "global-name", None, aliases.SCOPE_GLOBAL)
    store.set("3C:DC:75:00:00:01", "project-name", None, aliases.SCOPE_PROJECT)

    entry, scope = aliases.AliasStore.load().lookup("3C:DC:75:00:00:01")
    assert entry.name == "project-name"
    assert scope == aliases.SCOPE_PROJECT


def test_project_file_is_discovered_from_a_subdirectory(isolated_config, monkeypatch):
    store = aliases.AliasStore.load()
    store.set("3C:DC:75:00:00:01", "project-name", None, aliases.SCOPE_PROJECT)

    nested = isolated_config["project_dir"] / "src" / "deep"
    nested.mkdir(parents=True)
    monkeypatch.chdir(nested)

    reloaded = aliases.AliasStore.load()
    assert reloaded.project_file == isolated_config["project"]
    assert reloaded.lookup("3C:DC:75:00:00:01")[0].name == "project-name"


def test_duplicate_name_in_same_scope_is_rejected(isolated_config):
    store = aliases.AliasStore.load()
    store.set("3C:DC:75:00:00:01", "sensor-node", None, aliases.SCOPE_GLOBAL)

    with pytest.raises(aliases.AliasError, match="既に"):
        store.set("88:56:A6:00:00:02", "sensor-node", None, aliases.SCOPE_GLOBAL)

    # 大文字小文字だけ違う名前も同じ扱いにする（取り違えの元になるため）。
    with pytest.raises(aliases.AliasError):
        store.set("88:56:A6:00:00:02", "Sensor-Node", None, aliases.SCOPE_GLOBAL)


def test_renaming_the_same_device_is_allowed(isolated_config):
    store = aliases.AliasStore.load()
    store.set("3C:DC:75:00:00:01", "old-name", "メモ", aliases.SCOPE_GLOBAL)
    store.set("3C:DC:75:00:00:01", "new-name", None, aliases.SCOPE_GLOBAL)

    entry, _scope = aliases.AliasStore.load().lookup("3C:DC:75:00:00:01")
    assert entry.name == "new-name"
    # note を渡さない改名ではメモを消さない。
    assert entry.note == "メモ"


def test_remove_without_scope_clears_both(isolated_config):
    store = aliases.AliasStore.load()
    store.set("3C:DC:75:00:00:01", "g", None, aliases.SCOPE_GLOBAL)
    store.set("3C:DC:75:00:00:01", "p", None, aliases.SCOPE_PROJECT)

    removed = store.remove("3C:DC:75:00:00:01")
    assert {scope for _path, scope in removed} == {aliases.SCOPE_GLOBAL, aliases.SCOPE_PROJECT}
    assert aliases.AliasStore.load().lookup("3C:DC:75:00:00:01") == (None, None)


def test_apply_uses_the_strongest_available_key(isolated_config, fake_ports):
    """弱いキーで登録した名前が、MAC 判明後も引けること。"""
    fake_ports([BRIDGE_PORT])
    store = aliases.AliasStore.load()
    store.set("serial:0001", "bridge-board", None, aliases.SCOPE_GLOBAL)

    found = devmod.list_devices()
    found[0].mac = "AA:BB:CC:DD:EE:FF"  # --probe で判明した状況を模す
    aliases.apply(found, aliases.AliasStore.load())

    assert found[0].alias == "bridge-board"
    assert found[0].alias_key == "serial:0001"
    assert found[0].identity == "AA:BB:CC:DD:EE:FF"


def test_short_string_form_is_accepted_for_hand_written_files(isolated_config):
    isolated_config["project"].write_text(
        json.dumps({"aliases": {"3C:DC:75:00:00:01": "手書き"}}), encoding="utf-8"
    )
    entry, scope = aliases.AliasStore.load().lookup("3C:DC:75:00:00:01")

    assert entry.name == "手書き"
    assert entry.note is None
    assert scope == aliases.SCOPE_PROJECT


def test_broken_json_reports_the_path(isolated_config):
    isolated_config["project"].write_text("{ not json", encoding="utf-8")
    with pytest.raises(aliases.AliasError, match=aliases.PROJECT_FILENAME):
        aliases.AliasStore.load()


def test_find_by_name_locates_disconnected_devices(isolated_config):
    store = aliases.AliasStore.load()
    store.set("3C:DC:75:00:00:01", "sensor-node", None, aliases.SCOPE_GLOBAL)

    hits = aliases.AliasStore.load().find_by_name("SENSOR-NODE")
    assert [identity for identity, _entry, _scope in hits] == ["3C:DC:75:00:00:01"]


def test_legacy_global_file_is_read_and_migrated_on_write(isolated_config):
    legacy = isolated_config["legacy_global"]
    legacy.parent.mkdir(parents=True)
    legacy.write_text(
        json.dumps({"aliases": {"3C:DC:75:00:00:01": "legacy-name"}}),
        encoding="utf-8",
    )

    store = aliases.AliasStore.load()
    assert store.lookup("3C:DC:75:00:00:01")[0].name == "legacy-name"
    assert store.global_file == isolated_config["global"]

    store.set("88:56:A6:00:00:02", "new-name", None, aliases.SCOPE_GLOBAL)
    migrated = aliases.AliasStore.load()
    assert migrated.lookup("3C:DC:75:00:00:01")[0].name == "legacy-name"
    assert migrated.lookup("88:56:A6:00:00:02")[0].name == "new-name"


def test_legacy_project_file_is_read_and_migrated_on_write(isolated_config):
    isolated_config["legacy_project"].write_text(
        json.dumps({"aliases": {"3C:DC:75:00:00:01": "legacy-project"}}),
        encoding="utf-8",
    )

    store = aliases.AliasStore.load()
    assert store.project_file == isolated_config["project"]
    assert store.lookup("3C:DC:75:00:00:01")[0].name == "legacy-project"

    store.set("88:56:A6:00:00:02", "new-project", None, aliases.SCOPE_PROJECT)
    migrated = aliases.AliasStore.load()
    assert migrated.lookup("3C:DC:75:00:00:01")[0].name == "legacy-project"
    assert migrated.lookup("88:56:A6:00:00:02")[0].name == "new-project"
