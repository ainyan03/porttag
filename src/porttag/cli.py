"""porttag のコマンドライン。"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Optional, Sequence

from . import __version__, aliases, devices as devmod, links as linkmod, probe as probemod, render

EXIT_OK = 0
EXIT_NOT_FOUND = 1
EXIT_ENVIRONMENT = 3


def _eprint(message: str) -> None:
    print(message, file=sys.stderr)


def _load(include_all: bool, do_probe: bool, timeout: float) -> tuple[list, aliases.AliasStore]:
    """デバイスを列挙し、必要なら probe してからエイリアスを適用する。

    probe を先に済ませるのは、ブリッジ越しのボードで MAC が判明した場合に
    より強いキーでエイリアスを引けるようにするため。
    """
    found = devmod.list_devices(include_all=include_all)
    if do_probe and found:
        probemod.probe_devices(found, timeout=timeout)
    store = aliases.AliasStore.load()
    aliases.apply(found, store)
    # 名前は機体を探す手掛かりなので名前順に出す。名前は list_devices の後で付くためここで並べる。
    devmod.sort_by_name(found)
    _sync_links(found, store)
    return found, store


def _sync_links(found, store, create_project: bool = False) -> linkmod.SyncResult:
    """リンク置き場を同期する。失敗しても本来のコマンドは止めない。"""
    result = linkmod.sync(found, store, create_project=create_project)
    for warning in result.warnings:
        _eprint(warning)
    return result


def _resolve_one(found: Sequence, target: str) -> Optional[object]:
    hits = devmod.resolve(list(found), target)
    if not hits:
        _eprint(f"'{target}' に一致するデバイスが見つかりません。")
        return None
    if len(hits) > 1:
        _eprint(f"'{target}' は複数のデバイスに一致します:")
        for dev in hits:
            _eprint(f"  {dev.port}  {dev.identity}  {dev.alias or render.NO_NAME}")
        return None
    return hits[0]


def _scope_from_args(args, store: aliases.AliasStore) -> str:
    """書き込み先スコープ。明示指定がなければプロジェクトファイルの有無で決める。"""
    if getattr(args, "project", False):
        return aliases.SCOPE_PROJECT
    if getattr(args, "global_scope", False):
        return aliases.SCOPE_GLOBAL
    return aliases.SCOPE_PROJECT if store.project_file else aliases.SCOPE_GLOBAL


def cmd_list(args) -> int:
    try:
        found, store = _load(args.all, args.probe, args.timeout)
    except probemod.ProbeUnavailable as exc:
        _eprint(str(exc))
        return EXIT_ENVIRONMENT

    if args.json:
        print(
            render.format_json(
                found,
                probed=args.probe,
                global_file=str(store.global_file),
                project_file=str(store.project_file) if store.project_file else None,
            )
        )
        return EXIT_OK
    if args.plain:
        text = render.format_plain(found)
        if text:
            print(text)
        return EXIT_OK
    if args.tree:
        print(render.format_tree(found, probed=args.probe, color=render.use_color()))
        return EXIT_OK

    print(render.format_table(found, probed=args.probe, color=render.use_color()))
    return EXIT_OK


def cmd_info(args) -> int:
    try:
        found, store = _load(True, args.probe, args.timeout)
    except probemod.ProbeUnavailable as exc:
        _eprint(str(exc))
        return EXIT_ENVIRONMENT

    dev = _resolve_one(found, args.target)
    if dev is None:
        return EXIT_NOT_FOUND

    if args.json:
        print(json.dumps(dev.to_dict(), ensure_ascii=False, indent=2))
        return EXIT_OK

    fields = [
        ("name", dev.alias or render.NO_NAME),
        ("note", dev.note),
        ("alias scope", dev.alias_scope),
        ("alias key", dev.alias_key),
        ("port", dev.port),
        ("identity", dev.identity),
        ("identity kind", dev.identity_kind),
        ("mac", dev.mac),
        ("usb id", dev.usb_id),
        ("manufacturer", dev.manufacturer),
        ("product", dev.product),
        ("serial number", dev.serial_number),
        ("usb location", dev.location),
        ("interface", dev.interface_hint),
        ("chip", dev.chip_description or dev.chip),
        ("features", dev.features),
        ("flash size", dev.flash_size),
        ("probe error", dev.probe_error),
    ]
    width = max(len(label) for label, value in fields if value)
    for label, value in fields:
        if value:
            print(f"{label.ljust(width)} : {value}")
    return EXIT_OK


def cmd_name(args) -> int:
    try:
        found, store = _load(True, args.probe, args.timeout)
    except probemod.ProbeUnavailable as exc:
        _eprint(str(exc))
        return EXIT_ENVIRONMENT

    dev = _resolve_one(found, args.target)
    if dev is None:
        return EXIT_NOT_FOUND

    scope = _scope_from_args(args, store)
    # 既に別のキーで登録済みならそれを更新する。二重登録は取り違えの元。
    key = dev.alias_key or dev.identity
    try:
        path = store.set(key, args.name, args.note, scope)
    except aliases.AliasError as exc:
        _eprint(str(exc))
        return EXIT_NOT_FOUND

    _sync_links(found, store)
    print(f"{dev.port} ({key}) を '{args.name}' として登録しました → {path}")
    if not dev.is_stable_identity:
        _eprint(
            "注意: このデバイスは個体固有の ID を持たないため、USB の位置で識別しています。"
            " 挿す場所を変えると名前が別のデバイスに付きます。"
        )
    return EXIT_OK


def cmd_unname(args) -> int:
    found, store = _load(True, False, args.timeout)
    scope: Optional[str] = None
    if args.project:
        scope = aliases.SCOPE_PROJECT
    elif args.global_scope:
        scope = aliases.SCOPE_GLOBAL

    # 接続中のデバイスから引く。見つからなければ登録済みの名前から直接引く。
    keys: list[str] = []
    hits = devmod.resolve(found, args.target)
    if len(hits) == 1:
        keys = [hits[0].alias_key or hits[0].identity]
    elif len(hits) > 1:
        _eprint(f"'{args.target}' は複数のデバイスに一致します。ポート名か MAC を指定してください。")
        return EXIT_NOT_FOUND
    else:
        by_name = store.find_by_name(args.target)
        if by_name:
            keys = [identity for identity, _entry, _scope in by_name]
        elif args.target in store.merged():
            keys = [args.target]

    if not keys:
        _eprint(f"'{args.target}' に対応する登録が見つかりません。")
        return EXIT_NOT_FOUND

    removed_any = False
    for key in keys:
        try:
            for path, removed_scope in store.remove(key, scope):
                print(f"{key} の名前を削除しました（{removed_scope}） → {path}")
                removed_any = True
        except aliases.AliasError as exc:
            _eprint(str(exc))
            return EXIT_NOT_FOUND

    if not removed_any:
        _eprint(f"'{args.target}' は指定スコープに登録されていません。")
        return EXIT_NOT_FOUND
    _sync_links(found, store)
    return EXIT_OK


def cmd_port(args) -> int:
    """名前からポートパスだけを 1 行で返す。スクリプトからの利用が主目的。"""
    found, _store = _load(True, False, args.timeout)
    hits = devmod.resolve(found, args.target)
    if not hits:
        _eprint(f"'{args.target}' に一致する接続中のデバイスがありません。")
        return EXIT_NOT_FOUND
    if len(hits) > 1:
        _eprint(f"'{args.target}' は複数のデバイスに一致します。")
        for dev in hits:
            _eprint(f"  {dev.port}  {dev.identity}  {dev.alias or render.NO_NAME}")
        return EXIT_NOT_FOUND
    print(hits[0].port)
    return EXIT_OK


def cmd_aliases(args) -> int:
    found, store = _load(True, False, args.timeout)
    connected = {}
    for dev in found:
        for key in dev.identity_candidates:
            connected.setdefault(key, dev)

    entries = store.merged()
    if args.json:
        payload = {
            "alias_files": {
                "global": str(store.global_file),
                "project": str(store.project_file) if store.project_file else None,
            },
            "aliases": [
                {
                    "identity": identity,
                    "name": entry.name,
                    "note": entry.note,
                    "scope": scope,
                    "connected": identity in connected,
                    "port": connected[identity].port if identity in connected else None,
                }
                for identity, (entry, scope) in sorted(
                    entries.items(), key=lambda kv: devmod.name_sort_key(kv[1][0].name)
                )
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK

    if not entries:
        print("登録済みの名前はありません。")
        print(f"グローバル: {store.global_file}")
        if store.project_file:
            print(f"プロジェクト: {store.project_file}")
        return EXIT_OK

    rows = []
    for identity, (entry, scope) in sorted(
        entries.items(), key=lambda kv: devmod.name_sort_key(kv[1][0].name)
    ):
        dev = connected.get(identity)
        rows.append(
            [
                entry.name,
                identity,
                scope,
                dev.port if dev else "-",
                entry.note or "",
            ]
        )
    headers = ["NAME", "ID", "SCOPE", "PORT", "NOTE"]
    widths = [max(len(headers[i]), *(len(r[i]) for r in rows)) for i in range(len(headers))]
    print("  ".join(h.ljust(widths[i]) for i, h in enumerate(headers)).rstrip())
    for row in rows:
        print("  ".join(c.ljust(widths[i]) for i, c in enumerate(row)).rstrip())
    print()
    print(f"グローバル: {store.global_file}")
    print(f"プロジェクト: {store.project_file or '(なし)'}")
    return EXIT_OK


def cmd_link(args) -> int:
    """タグ名で参照できるシンボリックリンク置き場を作成・更新して一覧する。"""
    found, store = _load(True, False, args.timeout)
    project_dir = linkmod.project_link_dir(store)
    first_time = project_dir is not None and not project_dir.is_dir()
    result = _sync_links(found, store, create_project=store.project_file is not None)

    for label, farm in (("グローバル", result.global_farm), ("プロジェクト", result.project_farm)):
        if farm is None:
            continue
        print(f"{label}: {farm.directory}")
        if farm.links:
            for name, port in sorted(farm.links.items(), key=lambda kv: devmod.name_sort_key(kv[0])):
                print(f"  {name} -> {port}")
        else:
            print("  (接続中の名前付きデバイスなし)")
    if result.project_farm is None:
        print(
            f"プロジェクト: (なし; {aliases.PROJECT_FILENAME} のあるプロジェクトで実行すると作成)"
        )
    if first_time and result.project_farm is not None:
        print()
        print(
            f"ヒント: リンクはこのマシン固有です。{linkmod.PROJECT_LINK_ROOT}/ を"
            " .gitignore に追加してください。"
        )
    return EXIT_OK


def _add_scope_flags(parser: argparse.ArgumentParser) -> None:
    group = parser.add_mutually_exclusive_group()
    group.add_argument(
        "--project",
        action="store_true",
        help=f"プロジェクトの {aliases.PROJECT_FILENAME} を対象にする",
    )
    group.add_argument(
        "--global",
        dest="global_scope",
        action="store_true",
        help="グローバル設定を対象にする",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="porttag",
        description="USB 接続された ESP32 系デバイスを一覧し、個体に名前を付けて取り違えを防ぐ。",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""使用例:
  porttag
  porttag name usbmodem211101 sensor-node
  porttag port sensor-node

各サブコマンドの詳細:
  porttag <command> --help""",
    )
    parser.add_argument("--version", action="version", version=f"porttag {__version__}")

    # 全サブコマンドで共通のオプション。サブコマンド省略時にも通るよう parents で配る。
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--timeout",
        type=float,
        default=probemod.DEFAULT_TIMEOUT,
        help="esptool 1 台あたりのタイムアウト秒数 (既定: %(default)s)",
    )

    sub = parser.add_subparsers(dest="command")

    p_list = sub.add_parser(
        "list", parents=[common], help="接続中のデバイスを一覧する（既定のコマンド）"
    )
    p_list.add_argument("--json", action="store_true", help="JSON で出力する")
    p_list.add_argument("--plain", action="store_true", help="TSV（名前/ポート/ID）で出力する")
    p_list.add_argument(
        "--tree", action="store_true", help="USB ハブの段ごとに接続経路を木で表示する"
    )
    p_list.add_argument(
        "--probe",
        action="store_true",
        help="esptool でチップ種別まで確定させる（デバイスをリセットする）",
    )
    p_list.add_argument("--all", action="store_true", help="ESP32 以外のシリアルポートも表示する")
    p_list.set_defaults(func=cmd_list)

    p_info = sub.add_parser("info", parents=[common], help="1 台の詳細を表示する")
    p_info.add_argument("target", help="名前 / MAC / ポート名")
    p_info.add_argument("--probe", action="store_true", help="esptool で深掘りする（リセットする）")
    p_info.add_argument("--json", action="store_true", help="JSON で出力する")
    p_info.set_defaults(func=cmd_info)

    p_name = sub.add_parser("name", parents=[common], help="デバイスに名前を付ける")
    p_name.add_argument("target", help="名前 / MAC / ポート名")
    p_name.add_argument("name", help="付ける名前")
    p_name.add_argument("--note", help="用途などの補足メモ")
    p_name.add_argument(
        "--probe",
        action="store_true",
        help="登録前に esptool で MAC を確定させる（ブリッジ越しのボード向け。リセットする）",
    )
    _add_scope_flags(p_name)
    p_name.set_defaults(func=cmd_name)

    p_unname = sub.add_parser("unname", parents=[common], help="名前の登録を削除する")
    p_unname.add_argument("target", help="名前 / MAC / ポート名")
    _add_scope_flags(p_unname)
    p_unname.set_defaults(func=cmd_unname)

    p_port = sub.add_parser("port", parents=[common], help="名前からポートパスだけを出力する")
    p_port.add_argument("target", help="名前 / MAC / ポート名")
    p_port.set_defaults(func=cmd_port)

    p_link = sub.add_parser(
        "link",
        parents=[common],
        help="タグ名で参照できるシンボリックリンクを作成・更新する",
        description="接続中の名前付きデバイスへのシンボリックリンクを固定パスに張る。"
        " platformio.ini などパス文字列しか書けない設定からタグ名で参照するために使う。"
        " リンクは以後の porttag コマンド実行のたびに自動で同期される。",
    )
    p_link.set_defaults(func=cmd_link)

    p_aliases = sub.add_parser("aliases", parents=[common], help="登録済みの名前を一覧する（未接続も含む）")
    p_aliases.add_argument("--json", action="store_true", help="JSON で出力する")
    p_aliases.set_defaults(func=cmd_aliases)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = build_parser()
    argv = list(sys.argv[1:] if argv is None else argv)
    # サブコマンド省略時は list として扱う（`porttag --json` も通す）。
    known = {"list", "info", "name", "unname", "port", "link", "aliases"}
    if not argv or (argv[0] not in known and argv[0] not in ("-h", "--help", "--version")):
        argv = ["list"] + argv

    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        parser.print_help()
        return EXIT_OK
    try:
        return args.func(args)
    except aliases.AliasError as exc:
        _eprint(str(exc))
        return EXIT_ENVIRONMENT
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
