"""タグ名で参照できるシンボリックリンク置き場（link farm）の管理。

ポートのデバイスノード名（``/dev/cu.usbmodem211101`` など）は抜き差しで変わるが、
リンクのパスは変わらない。platformio.ini のように「パス文字列しか書けない設定」から
タグ名でデバイスを参照できるようにする。

- グローバル: ``$XDG_STATE_HOME/porttag/dev``（既定 ``~/.local/state/porttag/dev``）
- プロジェクト: ``<プロジェクトルート>/.porttag/dev``

リンクは porttag のコマンド実行のたびに同期される（遅延更新）。切断中のデバイスの
リンクは削除する。古いノード名を指したまま別のデバイスを開いてしまうより、
存在しないパスで失敗する方が安全なため。

プロジェクト側のディレクトリは ``porttag link`` を実行したときにだけ作成し、
以後は他のコマンドでも維持する。porttag を使っただけのリポジトリに勝手に
ディレクトリを増やさないため。
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .aliases import AliasEntry, AliasStore, is_safe_name

LINK_DIRNAME = "dev"
PROJECT_LINK_ROOT = ".porttag"


def global_link_dir() -> Path:
    base = os.environ.get("XDG_STATE_HOME")
    root = Path(base) if base else Path.home() / ".local" / "state"
    return root / "porttag" / LINK_DIRNAME


def project_link_dir(store: AliasStore) -> Optional[Path]:
    if store.project_file is None:
        return None
    return store.project_file.parent / PROJECT_LINK_ROOT / LINK_DIRNAME


@dataclass
class FarmState:
    directory: Path
    links: dict[str, str] = field(default_factory=dict)


@dataclass
class SyncResult:
    global_farm: Optional[FarmState] = None
    project_farm: Optional[FarmState] = None
    warnings: list[str] = field(default_factory=list)


def _desired(
    entries: dict[str, AliasEntry],
    connected: dict[str, object],
    warnings: list[str],
    scope_label: str,
) -> dict[str, str]:
    """接続中のデバイスに限定した「リンク名 → ポートパス」の集合を作る。"""
    by_key: dict[str, tuple[str, str]] = {}
    ambiguous: set[str] = set()
    for identity, entry in entries.items():
        dev = connected.get(identity)
        if dev is None:
            continue
        if not is_safe_name(entry.name):
            warnings.append(
                f"名前 '{entry.name}' はリンク名に使えないためスキップします（{scope_label}）"
            )
            continue
        # macOS の大文字小文字を区別しないファイルシステムでは別名でも同じ
        # リンクファイルになるため、小文字化したキーで衝突を検出する。
        key = entry.name.lower()
        if key in by_key and by_key[key][1] != dev.port:
            ambiguous.add(key)
            continue
        by_key[key] = (entry.name, dev.port)
    for key in ambiguous:
        name = by_key.pop(key)[0]
        warnings.append(
            f"名前 '{name}' が複数のデバイスを指すためリンクを作りません（{scope_label}）"
        )
    return {name: port for name, port in by_key.values()}


def _sync_dir(directory: Path, desired: dict[str, str], warnings: list[str]) -> dict[str, str]:
    directory.mkdir(parents=True, exist_ok=True)

    # 望ましい集合に無いシンボリックリンクは切断・改名の名残なので消す。
    # 通常のファイルはユーザーの持ち物かもしれないので触らない。
    for child in directory.iterdir():
        if child.is_symlink() and child.name not in desired:
            child.unlink(missing_ok=True)

    result: dict[str, str] = {}
    for name, port in sorted(desired.items()):
        link = directory / name
        try:
            if link.is_symlink():
                if os.readlink(link) == port:
                    result[name] = port
                    continue
            elif link.exists():
                warnings.append(f"{link} はシンボリックリンクではないため触りません")
                continue
            tmp = directory / f"{name}.porttag-tmp"
            tmp.unlink(missing_ok=True)
            os.symlink(port, tmp)
            os.replace(tmp, link)
            result[name] = port
        except OSError as exc:
            warnings.append(f"{link} を作成できません: {exc}")
    return result


def sync(found, store: AliasStore, *, create_project: bool = False) -> SyncResult:
    """リンク置き場を現在の接続状態に同期する。

    グローバル側は常に維持する。プロジェクト側はディレクトリが既にあるか
    ``create_project`` が指定された場合のみ維持する。
    """
    result = SyncResult()

    connected: dict[str, object] = {}
    for dev in found:
        for key in dev.identity_candidates:
            connected.setdefault(key, dev)

    try:
        gdir = global_link_dir()
        gdesired = _desired(store.global_entries, connected, result.warnings, "global")
        result.global_farm = FarmState(gdir, _sync_dir(gdir, gdesired, result.warnings))
    except OSError as exc:
        result.warnings.append(f"グローバルのリンク置き場を更新できません: {exc}")

    pdir = project_link_dir(store)
    if pdir is not None and (create_project or pdir.is_dir()):
        try:
            # プロジェクト側はマージ後の視点（プロジェクト優先）。platformio.ini から
            # 参照する名前がグローバル登録でも解決できるようにするため。
            merged = dict(store.global_entries)
            merged.update(store.project_entries)
            pdesired = _desired(merged, connected, result.warnings, "project")
            result.project_farm = FarmState(pdir, _sync_dir(pdir, pdesired, result.warnings))
        except OSError as exc:
            result.warnings.append(f"プロジェクトのリンク置き場を更新できません: {exc}")

    return result
