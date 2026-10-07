"""一覧の表示（テーブル / JSON / スクリプト向けの素の出力）。"""

from __future__ import annotations

import json
import os
import sys
import re
import unicodedata
from typing import Any, Optional, Sequence

from .devices import KIND_BRIDGE, KIND_ESPRESSIF, KIND_OTHER, Device

NO_NAME = "(no name)"

_RESET = "\033[0m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_YELLOW = "\033[33m"
_RED = "\033[31m"
_CYAN = "\033[36m"


def use_color(stream=None) -> bool:
    """色を付けてよいか。NO_COLOR と非 TTY を尊重する。"""
    if os.environ.get("NO_COLOR"):
        return False
    stream = stream or sys.stdout
    return bool(getattr(stream, "isatty", lambda: False)())


def _display_width(text: str) -> int:
    """全角文字を 2 桁として数えた表示幅。ノートに日本語を書いても崩れないようにする。"""
    return sum(2 if unicodedata.east_asian_width(ch) in "WF" else 1 for ch in text)


def _pad(text: str, width: int) -> str:
    return text + " " * max(0, width - _display_width(text))


def _type_column(dev: Device, probed: bool) -> str:
    """種別列。probe 済みなら確定したチップ名、未 probe なら USB から分かる範囲。"""
    if probed:
        if dev.chip_description:
            return dev.chip_description
        if dev.chip:
            return dev.chip
        # probe に失敗しても、USB から分かる範囲は捨てずに出す（理由は脚注に載る）。
    if dev.interface_hint:
        return dev.interface_hint
    if dev.product:
        return dev.product
    return "-"


def _id_column(dev: Device) -> str:
    if dev.mac:
        return dev.mac
    return dev.identity


#: 表示で省く OS 固有のポート接頭辞。macOS の pyserial は IOCalloutDevice だけを返すので常に /dev/cu.、
#: Linux は /dev/ttyUSB0 等 (/dev/tty まで削ると USB0 になり読めないので /dev/ のみ)、Windows は COM3 で接頭辞なし。
_PORT_PREFIXES = {"darwin": "/dev/cu.", "linux": "/dev/"}


def port_prefix(devices: Sequence[Device], platform: str = sys.platform) -> str:
    """一覧の全ポートに共通する省略可能な接頭辞。1 台でも外れれば省略しない (混在表示を避ける)。"""
    prefix = _PORT_PREFIXES.get(platform, "")
    if prefix and devices and all(d.port.startswith(prefix) for d in devices):
        return prefix
    return ""


def _short_port(dev: Device, prefix: str) -> str:
    return dev.port[len(prefix):] if prefix else dev.port


def format_table(devices: Sequence[Device], probed: bool = False, color: bool = False) -> str:
    if not devices:
        return "ESP32 系のデバイスは見つかりませんでした。"

    show_flash = probed and any(d.flash_size for d in devices)
    show_note = any(d.note for d in devices)

    prefix = port_prefix(devices)
    # 表示だけの省略なので、気づかずコピーされないようヘッダで省略を明示する。
    headers = ["NAME", f"PORT ({prefix} 省略)" if prefix else "PORT", "ID", "TYPE"]
    if show_flash:
        headers.append("FLASH")
    if show_note:
        headers.append("NOTE")

    rows: list[list[str]] = []
    for dev in devices:
        row = [
            dev.alias or NO_NAME,
            _short_port(dev, prefix),
            _id_column(dev),
            _type_column(dev, probed),
        ]
        if show_flash:
            row.append(dev.flash_size or "-")
        if show_note:
            row.append(dev.note or "")
        rows.append(row)

    widths = [
        max(_display_width(headers[i]), *(_display_width(r[i]) for r in rows))
        for i in range(len(headers))
    ]

    def line(cells: Sequence[str]) -> str:
        parts = [_pad(c, widths[i]) for i, c in enumerate(cells)]
        return "  ".join(parts).rstrip()

    out: list[str] = []
    header_line = line(headers)
    out.append(f"{_BOLD}{header_line}{_RESET}" if color else header_line)

    for dev, row in zip(devices, rows):
        text = line(row)
        if not color:
            out.append(text)
            continue
        if dev.probe_error:
            out.append(f"{_RED}{text}{_RESET}")
        elif dev.alias is None:
            out.append(f"{_YELLOW}{text}{_RESET}")
        else:
            out.append(text)

    # 合計行は「挿した台数と一致するか」を照合するためのもの。注記 (DIM) とは役割が違うので通常色で出す。
    out.append("")
    out.append(_summary_line(devices))

    notes = _footnotes(devices, probed)
    if notes:
        out.extend(f"{_DIM}{n}{_RESET}" if color else n for n in notes)
    return "\n".join(out)


def _summary_line(devices: Sequence[Device]) -> str:
    """検出台数の 1 行。--all で ESP32 以外が混ざるときは内訳を分け、「台」の意味をぶらさない。"""
    esp = sum(1 for d in devices if d.kind != KIND_OTHER)
    extras: list[str] = []
    if esp != len(devices):
        extras.append(f"うち ESP32 系 {esp} 台")
    failed = sum(1 for d in devices if d.probe_error)
    if failed:
        extras.append(f"probe 失敗 {failed} 台")
    unit = "台" if esp == len(devices) else "ポート"
    text = f"{len(devices)} {unit}を検出しました"
    if extras:
        text += "（" + "、".join(extras) + "）"
    return text + "。"


def _footnotes(devices: Sequence[Device], probed: bool) -> list[str]:
    """取り違えにつながる曖昧さを明示する注記。"""
    notes: list[str] = []

    unnamed = [d for d in devices if d.alias is None]
    if unnamed:
        example = unnamed[0]
        notes.append(
            f"名前なしのデバイスが {len(unnamed)} 台あります: "
            f"porttag name {example.port} <名前> で登録できます。"
        )

    weak = [d for d in devices if not d.is_stable_identity]
    if weak:
        notes.append(
            f"{len(weak)} 台は個体固有の ID を持ちません（USB の位置で識別中）。"
            " 挿す場所を変えると別デバイスを指します。--probe で MAC を取得できます。"
        )

    if not probed and any(d.kind in (KIND_ESPRESSIF, KIND_BRIDGE) for d in devices):
        notes.append(
            "チップ種別は USB 情報だけでは確定しません。"
            " --probe を付けると esptool で確定できます（デバイスはリセットされます）。"
        )

    errors = [d for d in devices if d.probe_error]
    for dev in errors:
        notes.append(f"{dev.port}: {dev.probe_error}")

    return notes


_LOCATION_RE = re.compile(r"^(?P<bus>[^-]+)-(?P<path>[0-9]+(?:\.[0-9]+)*)")


def _parse_location(location: Optional[str]) -> Optional[tuple[str, tuple[int, ...]]]:
    """pyserial の location ("2-1.1.4.3", Linux では "1-1.4:1.0") をバスとポート経路に分ける。"""
    if not location:
        return None
    m = _LOCATION_RE.match(location)
    if not m:
        return None
    return m.group("bus"), tuple(int(x) for x in m.group("path").split("."))


class _Node:
    """USB ツリーの 1 ノード。子を持てばハブ、device を持てば末端。"""

    def __init__(self) -> None:
        self.children: dict[int, _Node] = {}
        self.device: Optional[Device] = None


def _build_tree(devices: Sequence[Device]) -> tuple[dict[str, _Node], list[Device]]:
    buses: dict[str, _Node] = {}
    unknown: list[Device] = []
    for dev in devices:
        parsed = _parse_location(dev.location)
        if parsed is None:
            unknown.append(dev)
            continue
        bus, path = parsed
        node = buses.setdefault(bus, _Node())
        for port in path:
            node = node.children.setdefault(port, _Node())
        node.device = dev
    return buses, unknown


def format_tree(devices: Sequence[Device], probed: bool = False, color: bool = False) -> str:
    """USB の接続経路をハブの段ごとに木で表示する。多段ハブでどの口に何が挿さっているかを見るためのもの。"""
    if not devices:
        return "ESP32 系のデバイスは見つかりませんでした。"

    buses, unknown = _build_tree(devices)

    # 1 行 = (木の部分, デバイス or None)。列揃えは木の部分の幅を揃えてから行う。
    lines: list[tuple[str, Optional[Device]]] = []
    headings: set[int] = set()  # バス / 経路不明 の見出し行 (色付き時に太字にする) の添字

    def walk(node: _Node, prefix: str, label: str, last: bool, root: bool) -> None:
        branch = "" if root else ("└ " if last else "├ ")
        # 子が 1 本だけ続く区間は "1.1.4" のように 1 行へ畳む (段数を無駄に増やさない)。
        while node.device is None and len(node.children) == 1:
            port, child = next(iter(node.children.items()))
            label = f"{label}.{port}" if label else str(port)
            node = child
        if node.device is not None:
            lines.append((f"{prefix}{branch}{label}", node.device))
            return
        lines.append((f"{prefix}{branch}{label}  ハブ", None))
        child_prefix = "" if root else prefix + ("  " if last else "│ ")
        ports = sorted(node.children)
        for i, port in enumerate(ports):
            walk(node.children[port], child_prefix, str(port), i == len(ports) - 1, False)

    for bus in sorted(buses, key=lambda b: (len(b), b)):
        headings.add(len(lines))
        lines.append((f"バス {bus}", None))
        root = buses[bus]
        ports = sorted(root.children)
        for i, port in enumerate(ports):
            walk(root.children[port], "", str(port), i == len(ports) - 1, False)

    if unknown:
        if lines:
            lines.append(("", None))  # 木の続きに見えないよう 1 行空ける
        headings.add(len(lines))
        lines.append(("経路不明 (USB の位置情報なし)", None))
        for i, dev in enumerate(unknown):
            lines.append((("└ " if i == len(unknown) - 1 else "├ ") + "?", dev))

    # 列揃えの幅はデバイス行だけで決める (ハブ行の「ハブ」まで含めると葉の行に無駄な空白が入る)。
    tree_width = max(_display_width(t) for t, d in lines if d is not None)
    prefix = port_prefix(devices)
    cells = {
        id(d): [d.alias or NO_NAME, _short_port(d, prefix), _id_column(d), _type_column(d, probed)]
        for _, d in lines if d is not None
    }
    widths = [max(_display_width(c[i]) for c in cells.values()) for i in range(4)]

    out: list[str] = []
    for idx, (text, dev) in enumerate(lines):
        if dev is None:
            out.append(f"{_BOLD}{text}{_RESET}" if color and idx in headings else text)
            continue
        row = cells[id(dev)]
        line = "  ".join([_pad(text, tree_width)] + [_pad(c, widths[i]) for i, c in enumerate(row)]).rstrip()
        if color and dev.probe_error:
            line = f"{_RED}{line}{_RESET}"
        elif color and dev.alias is None:
            line = f"{_YELLOW}{line}{_RESET}"
        out.append(line)

    out.append("")
    out.append(_summary_line(devices))
    notes = _footnotes(devices, probed)
    if prefix:
        # 木にはヘッダ行が無いので、省略の明示は注記で行う。
        notes.insert(0, f"ポートは {prefix} を省略して表示しています。")
    out.extend(f"{_DIM}{n}{_RESET}" if color else n for n in notes)
    return "\n".join(out)


def format_json(
    devices: Sequence[Device],
    probed: bool,
    global_file: Optional[str] = None,
    project_file: Optional[str] = None,
) -> str:
    payload: dict[str, Any] = {
        "probed": probed,
        "count": len(devices),
        "alias_files": {"global": global_file, "project": project_file},
        "devices": [d.to_dict() for d in devices],
    }
    return json.dumps(payload, ensure_ascii=False, indent=2)


def format_plain(devices: Sequence[Device]) -> str:
    """スクリプトで扱いやすい TSV。名前・ポート・ID の 3 列のみ。"""
    return "\n".join(
        "\t".join((d.alias or "", d.port, _id_column(d))) for d in devices
    )


def colorize(text: str, code: str, color: bool) -> str:
    return f"{code}{text}{_RESET}" if color else text


CYAN = _CYAN
YELLOW = _YELLOW
RED = _RED
DIM = _DIM
BOLD = _BOLD
