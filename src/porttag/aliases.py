"""デバイスに人間が読める名前を付けるエイリアスの保存と読み出し。

保存先は 2 系統ある。

- グローバル: ``$XDG_CONFIG_HOME/porttag/aliases.json``（既定 ``~/.config/...``）
- プロジェクト: カレントから上位に向かって探した ``.porttag.json``

同じデバイスが両方にあればプロジェクト側を優先する。プロジェクト用途では
「このリポジトリでの役割名」がグローバルな呼び名より優先されるべきため。
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional

PROJECT_FILENAME = ".porttag.json"
LEGACY_PROJECT_FILENAME = ".devusb-name.json"
CONFIG_DIRNAME = "porttag"
LEGACY_CONFIG_DIRNAME = "devusb-name"
SCOPE_PROJECT = "project"
SCOPE_GLOBAL = "global"

FORMAT_VERSION = 1


class AliasError(Exception):
    """エイリアス操作の失敗。CLI がメッセージをそのまま表示する。"""


def is_safe_name(name: str) -> bool:
    """リンクのファイル名としても安全に使える名前か。"""
    if not name or name in (".", ".."):
        return False
    return not any(c in name for c in ("/", "\\", "\x00"))


@dataclass
class AliasEntry:
    name: str
    note: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"name": self.name}
        if self.note:
            data["note"] = self.note
        return data


def global_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / CONFIG_DIRNAME / "aliases.json"


def legacy_global_path() -> Path:
    """devusb-name 時代のグローバル設定パス。移行時の読み込みにのみ使う。"""
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / LEGACY_CONFIG_DIRNAME / "aliases.json"


def _find_named_project_file(filename: str, start: Optional[Path] = None) -> Optional[Path]:
    current = (start or Path.cwd()).resolve()
    for directory in (current, *current.parents):
        candidate = directory / filename
        if candidate.is_file():
            return candidate
    return None


def find_project_path(start: Optional[Path] = None) -> Optional[Path]:
    """カレントから上位へ ``.porttag.json`` を探す。見つからなければ None。"""
    return _find_named_project_file(PROJECT_FILENAME, start)


def find_legacy_project_path(start: Optional[Path] = None) -> Optional[Path]:
    """devusb-name 時代のプロジェクト設定を探す。"""
    return _find_named_project_file(LEGACY_PROJECT_FILENAME, start)


def default_project_path(start: Optional[Path] = None) -> Path:
    """プロジェクトファイルの書き込み先。既存があればそこ、無ければカレント直下。"""
    found = find_project_path(start)
    if found:
        return found
    return (start or Path.cwd()).resolve() / PROJECT_FILENAME


def _load_file(path: Optional[Path]) -> dict[str, AliasEntry]:
    if path is None or not path.is_file():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AliasError(f"{path} を読めません: {exc}") from exc

    entries = raw.get("aliases", {}) if isinstance(raw, dict) else {}
    if not isinstance(entries, dict):
        raise AliasError(f"{path} の aliases が不正です（オブジェクトである必要があります）")

    result: dict[str, AliasEntry] = {}
    for identity, value in entries.items():
        if isinstance(value, str):
            result[identity] = AliasEntry(name=value)
        elif isinstance(value, dict) and isinstance(value.get("name"), str):
            note = value.get("note")
            result[identity] = AliasEntry(
                name=value["name"], note=note if isinstance(note, str) else None
            )
    return result


def _save_file(path: Path, entries: dict[str, AliasEntry]) -> None:
    payload = {
        "version": FORMAT_VERSION,
        "aliases": {k: v.to_dict() for k, v in sorted(entries.items())},
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        tmp.replace(path)
    except OSError as exc:
        raise AliasError(f"{path} へ書き込めません: {exc}") from exc


@dataclass
class AliasStore:
    """グローバルとプロジェクトの両方を束ねたビュー。"""

    global_file: Path
    project_file: Optional[Path]
    global_entries: dict[str, AliasEntry]
    project_entries: dict[str, AliasEntry]

    @classmethod
    def load(cls, start: Optional[Path] = None) -> "AliasStore":
        project_file = find_project_path(start)
        project_source = project_file
        if project_file is None:
            legacy_project = find_legacy_project_path(start)
            if legacy_project is not None:
                project_source = legacy_project
                # 旧設定は読めるようにするが、次回の書き込みは新名で保存する。
                project_file = legacy_project.with_name(PROJECT_FILENAME)
        gpath = global_path()
        gsource = gpath if gpath.is_file() else legacy_global_path()
        return cls(
            global_file=gpath,
            project_file=project_file,
            global_entries=_load_file(gsource),
            project_entries=_load_file(project_source),
        )

    def lookup(self, identity: str) -> tuple[Optional[AliasEntry], Optional[str]]:
        """identity に対応する名前と、それがどちらのスコープ由来かを返す。"""
        if identity in self.project_entries:
            return self.project_entries[identity], SCOPE_PROJECT
        if identity in self.global_entries:
            return self.global_entries[identity], SCOPE_GLOBAL
        return None, None

    def lookup_any(
        self, identities: Iterable[str]
    ) -> tuple[Optional[AliasEntry], Optional[str], Optional[str]]:
        """候補キーを強い順に試し、最初に見つかった名前とスコープと使ったキーを返す。"""
        for identity in identities:
            entry, scope = self.lookup(identity)
            if entry:
                return entry, scope, identity
        return None, None, None

    def merged(self) -> dict[str, tuple[AliasEntry, str]]:
        """プロジェクト優先でマージした全エントリ。"""
        merged: dict[str, tuple[AliasEntry, str]] = {
            k: (v, SCOPE_GLOBAL) for k, v in self.global_entries.items()
        }
        merged.update({k: (v, SCOPE_PROJECT) for k, v in self.project_entries.items()})
        return merged

    def find_by_name(self, name: str) -> list[tuple[str, AliasEntry, str]]:
        """名前から identity を逆引きする（接続されていないデバイスも引ける）。"""
        lowered = name.lower()
        hits = [
            (identity, entry, scope)
            for identity, (entry, scope) in self.merged().items()
            if entry.name.lower() == lowered
        ]
        return hits

    def _target(self, scope: str) -> tuple[Path, dict[str, AliasEntry]]:
        if scope == SCOPE_PROJECT:
            path = self.project_file or default_project_path()
            return path, self.project_entries
        return self.global_file, self.global_entries

    def set(self, identity: str, name: str, note: Optional[str], scope: str) -> Path:
        # 名前はシンボリックリンクのファイル名にもなるため、パスとして
        # 危険な文字列は登録段階で拒否する。
        if not is_safe_name(name):
            raise AliasError(
                f"名前 '{name}' は使えません（空文字・'.'・'..'・パス区切りを含む名前は不可）。"
            )
        path, entries = self._target(scope)
        # 同じスコープ内での名前の重複は取り違えの元なので拒否する。
        for other, entry in entries.items():
            if other != identity and entry.name.lower() == name.lower():
                raise AliasError(
                    f"名前 '{name}' は既に {other} に使われています（{scope}）。"
                    " 先に unname するか別の名前にしてください。"
                )
        existing = entries.get(identity)
        entries[identity] = AliasEntry(
            name=name, note=note if note is not None else (existing.note if existing else None)
        )
        _save_file(path, entries)
        if scope == SCOPE_PROJECT:
            self.project_file = path
        return path

    def remove(self, identity: str, scope: Optional[str] = None) -> list[tuple[Path, str]]:
        """エイリアスを削除する。scope 未指定なら両方から消す。"""
        removed: list[tuple[Path, str]] = []
        scopes: Iterable[str] = (scope,) if scope else (SCOPE_PROJECT, SCOPE_GLOBAL)
        for target_scope in scopes:
            if target_scope == SCOPE_PROJECT and self.project_file is None:
                continue
            path, entries = self._target(target_scope)
            if identity in entries:
                del entries[identity]
                _save_file(path, entries)
                removed.append((path, target_scope))
        return removed


def apply(devices, store: AliasStore) -> None:
    """デバイス一覧にエイリアスを適用する（devices を破壊的に更新）。"""
    for dev in devices:
        entry, scope, key = store.lookup_any(dev.identity_candidates)
        if entry:
            dev.alias = entry.name
            dev.note = entry.note
            dev.alias_scope = scope
            dev.alias_key = key
