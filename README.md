# porttag

USB 接続された ESP32 系デバイスを一覧し、個体ごとに名前を付けて**取り違えを防ぐ** CLI ツール。

複数の ESP32 を同時に繋いだプロジェクトでは、`/dev/cu.usbmodem211201` のようなポート名は
挿す順序や USB ハブの状態で入れ替わる。書き込み先を間違えれば、別の機器のファームウェアを
上書きしてしまう。このツールは各デバイスを**個体固有の ID（MAC アドレス）**で識別し、
人間や AI エージェントが読める名前に対応付ける。

```
$ porttag
NAME          PORT (/dev/cu. 省略)  ID                 TYPE             NOTE
sensor-node   usbmodem211101        3C:DC:75:00:00:01  USB-Serial/JTAG  居間の温湿度計
display-unit  usbmodem211201        88:56:A6:00:00:02  USB-Serial/JTAG  M5Stack Core2
(no name)     usbmodem2141401       44:1B:F6:00:00:03  USB-Serial/JTAG
```

表のポート列は OS 固有の接頭辞（macOS は `/dev/cu.`、Linux は `/dev/`）を省いて表示する。
省いた接頭辞はヘッダに示す。`--plain` / `--json` / `port` サブコマンドは完全パスのまま返す。

## 何を根拠に識別しているか

ESP32-S3 / C3 / C6 など**チップ内蔵 USB** で繋がったデバイスは、USB のシリアル番号として
自身の MAC アドレスを申告する。つまり**デバイスに一切触れずに個体を特定できる**。
これが本ツールの既定の動作で、実行中のファームウェアを止めない。

一方、以下は USB 記述子だけでは分からない。

| 知りたいこと | 非破壊で分かるか |
|---|---|
| 個体の識別（MAC） | 内蔵 USB なら **可**／USB-UART ブリッジ経由は不可 |
| チップ種別（S3 / C3 / C6 ...） | **不可**（PID はチップ間で共通） |
| フラッシュ容量 | **不可** |

これらが必要なときだけ `--probe` を付ける。esptool でブートローダに落として問い合わせるため、
**対象デバイスはリセットされ、実行中のファームウェアが中断される**。既定では実行しない。

識別子は強い順に MAC → USB シリアル番号 → USB の物理位置 が選ばれる。
物理位置しか使えないデバイス（シリアル番号を持たない CH340 など）は挿す場所を変えると
別のデバイスを指すため、その旨が一覧に注記される。

## インストール

コマンドをどのディレクトリからでも使えるようにするには `pipx` を推奨する。
このリポジトリのルートで実行する場合は次のとおり。

```bash
pipx install .
porttag --version
```

別のディレクトリからインストールする場合は、チェックアウト先を指定する。

```bash
pipx install /path/to/porttag
```

通常の `pipx install` は、その時点のソースを独立した仮想環境へインストールする。
元のソースを後から編集しても自動では反映されない。更新後の内容を入れ直すには次を実行する。

```bash
pipx install --force /path/to/porttag
```

`porttag: command not found` になる場合は `pipx ensurepath` を実行し、シェルを開き直す。
アンインストールは `pipx uninstall porttag`。

特定のPython仮想環境内だけで使う場合は、代わりにその環境へインストールできる。

```bash
python -m pip install .
```

依存は `pyserial` のみ。`--probe` を使う場合は別途 `esptool` が必要
（`pipx install esptool`、または Arduino / ESP-IDF 付属のものを
環境変数 `PORTTAG_ESPTOOL` で指定）。esptool は v4 / v5 の両方に対応する。

## 使い方

### コマンド自身からヘルプを見る

基本的な構文と利用可能なサブコマンドは、外部ドキュメントを開かずに確認できる。

```bash
porttag --help             # 全体の概要
porttag name --help        # name の引数とオプション
porttag info --help        # 他のサブコマンドも同様
porttag --version          # バージョン
```

ヘルプには `help` サブコマンドではなく `-h` または `--help` を使う。
引数なしの `porttag` はヘルプ表示ではなく、既定の `list` としてデバイスを一覧する。

### コマンド一覧

```bash
porttag                              # 一覧（既定のコマンド）
porttag list                         # 上と同じ
porttag --json                       # JSON で出力
porttag --plain                      # TSV（名前 / ポート / ID）
porttag --tree                       # USB ハブの段ごとに接続経路を木で表示
porttag --probe                      # チップ種別まで確定させる（リセットする）
porttag --all                        # ESP32 以外のシリアルポートも表示

porttag name <対象> <名前> [--note メモ]   # 名前を付ける
porttag unname <対象>                      # 名前を消す
porttag port <名前>                        # 名前からポートパスだけを出力
porttag link                               # タグ名で参照できるシンボリックリンクを作成・更新
porttag aliases                            # 登録済みの名前（未接続も含む）
porttag aliases --json                     # 登録済みの名前を JSON で出力
porttag info <対象> [--probe]              # 1 台の詳細
porttag info <対象> --json                 # 1 台の詳細を JSON で出力
```

`<対象>` には **名前 / MAC / 登録済みID / ポートのフルパス / ポート名の一部** のいずれも使える。
複数に一致する場合は推測せずエラーにする（取り違え防止のため）。

```bash
porttag name usbmodem211101 sensor-node --note "居間の温湿度計"
porttag port sensor-node
# → /dev/cu.usbmodem211101
```

### 多段ハブのどの口に挿さっているかを見る

`--tree` は USB の接続経路（pyserial の location）をハブの段ごとに木にする。
ハブ自体はシリアルデバイスではないので名前は出ず「ハブ」とだけ表示する。
位置情報が取れないポート（Bluetooth 等）は末尾の「経路不明」にまとめる。

```
$ porttag --tree
バス 2
└ 1.1.4  ハブ
  ├ 1  ハブ
  │ ├ 1  sensor-node   usbmodem21141101  DC:54:75:00:00:04  USB-Serial/JTAG
  │ └ 2  display-unit  usbmodem21141201  98:88:E0:00:00:05  USB-Serial/JTAG
  └ 2  ハブ
    └ 1  (no name)     usbserial-0001    serial:0001        Silicon Labs CP210x

3 台を検出しました。
ポートは /dev/cu. を省略して表示しています。
```

### 書き込み先を名前で指定する

`port` サブコマンドはポートパスだけを 1 行で返すので、そのまま他のツールに渡せる。

```bash
esptool --port "$(porttag port sensor-node)" write-flash 0x0 firmware.bin
idf.py -p "$(porttag port sensor-node)" flash monitor
arduino-cli upload -p "$(porttag port display-unit)" --fqbn esp32:esp32:esp32s3 .
```

見つからない場合・複数に一致する場合は終了コード 1 で何も出力しない。
ただし `$(...)` を引数へ直接埋め込む形では、この失敗は `set -e` に拾われない
（空文字が渡り、ツール側のポートオープン失敗で止まる）。スクリプトで確実に
手前で止めたい場合は代入形にする。

```bash
PORT=$(porttag port sensor-node)   # 失敗すると set -e はここで止まる
esptool --port "$PORT" write-flash 0x0 firmware.bin
```

### 固定パスで参照する（platformio.ini など）

`upload_port` のように**パス文字列しか書けない設定**からタグ名を使うには、
`porttag link` でシンボリックリンクを作る。

```bash
porttag link
```

| スコープ | リンクの場所 | 名前の範囲 |
|---|---|---|
| グローバル | `$XDG_STATE_HOME/porttag/dev/<名前>`（既定 `~/.local/state/...`） | グローバル別名のみ |
| プロジェクト | プロジェクトルートの `.porttag/dev/<名前>` | プロジェクト＋グローバル（プロジェクト優先） |

プロジェクト側はプロジェクトルートからの相対パスで参照できるので、
`platformio.ini` にそのまま書ける。

```ini
[env:sensor]
upload_port = .porttag/dev/sensor-node
monitor_port = .porttag/dev/sensor-node
```

#### チップ内蔵 USB（USB-Serial/JTAG）のデバイスに書き込む場合

esptool はポートが USB-Serial/JTAG かどうかを USB の PID で判定してリセット手順を
切り替えるが、この判定は `/dev/` 直下のパスでしか働かない（esptool v5.1 時点。
macOS では `/dev` にリンクを置けない）。そのためリンク経由で書き込むときは
リセット手順を明示する必要がある。

```bash
esptool --before usb-reset --port ~/.local/state/porttag/dev/sensor-node write-flash 0x0 firmware.bin
```

esptool v4 系では綴りが `usb_reset`。PlatformIO では次の指定が対応する
（PlatformIO 同梱の esptool は v4 系。手元未実測）。

```ini
board_upload.before_reset = usb_reset
```

USB-UART ブリッジ（CH340 / CP210x など）のボードはリセット回路が独立しているため
この指定は不要で、付けてはいけない（正しくリセットされなくなる）。
モニタ用途（`monitor_port`）はリセットを伴わないので、どちらのデバイスでも
指定なしでリンクをそのまま使える。

リンクは **porttag のコマンドを実行するたびに現在の接続状態へ同期される**（遅延更新）。
デバイスを抜き差ししてポート名が変わったら、書き込みの前に一度 `porttag`（引数なしでよい）を
実行してリンクを張り直す。切断中のデバイスのリンクは削除されるため、古いポートを指したまま
別のデバイスへ書き込んでしまうことはなく、「パスが存在しない」というエラーで安全に失敗する。

プロジェクト側の `.porttag/dev/` は `porttag link` を一度実行したときにだけ作成され、
以後は他のコマンドでも自動で維持される。リンクはこのマシン固有なので
`.porttag/` は `.gitignore` に追加する（`.porttag.json` はコミット対象のままでよい）。

なお、シェルスクリプトやエージェントのように実行の瞬間にコマンドを呼べる場面では、
リンクより `porttag port` による使用時解決の方が確実で、こちらを推奨する。
リンクはフック手段を持たないツールのための出口という位置付け。

### AI エージェントから使う

`--json` が機械可読の正本。`identity` が個体固有キー、`stable_identity` が
その識別子を信用してよいかを示す。

```bash
porttag --json
```

```json
{
  "probed": false,
  "count": 2,
  "alias_files": {
    "global": "/Users/alice/.config/porttag/aliases.json",
    "project": "/Users/alice/work/my-project/.porttag.json"
  },
  "devices": [
    {
      "identity": "3C:DC:75:00:00:01",
      "identity_kind": "mac",
      "stable_identity": true,
      "alias": "sensor-node",
      "note": "居間の温湿度計",
      "port": "/dev/cu.usbmodem211101",
      "mac": "3C:DC:75:00:00:01",
      "usb_id": "303a:1001",
      "chip": null
    }
  ]
}
```

`alias_files` のパスは実際の出力では絶対パスになる。プロジェクト設定が見つからない場合、
`project` は `null`。

エージェントへの指示としては、次の運用を推奨する。

1. 書き込み前に必ず `porttag port <名前>` でポートを解決する（ポート名を直書きしない）
2. `alias` が `null` のデバイスには書き込まない（どの機器か確定していないため）
3. チップ種別が必要なときだけ `--probe` を使う（デバイスが再起動する副作用を理解した上で）

## 名前の保存先

| スコープ | 場所 | 用途 |
|---|---|---|
| プロジェクト | カレントから上位に探した `.porttag.json` | そのリポジトリでの役割名。リポジトリに commit して共有できる |
| グローバル | `$XDG_CONFIG_HOME/porttag/aliases.json`（既定 `~/.config/...`） | 手元の機材の呼び名 |

同じデバイスが両方にあればプロジェクト側が優先される。書き込み先は
プロジェクトファイルがあればそちら、なければグローバル。`--project` / `--global` で明示指定できる。

旧名 `devusb-name` の設定（`.devusb-name.json` と
`~/.config/devusb-name/aliases.json`）も読み取れる。旧設定だけが存在する場合は、
次に名前を変更した時点で `porttag` の新しい保存先へ内容を引き継ぐ。

同一スコープ内で同じ名前を 2 台に付けることはできない（大文字小文字の違いも同名として扱う）。

ファイル形式は素直な JSON なので手で編集してもよい。

```json
{
  "version": 1,
  "aliases": {
    "3C:DC:75:00:00:01": { "name": "sensor-node", "note": "居間の温湿度計" },
    "88:56:A6:00:00:02": "display-unit"
  }
}
```

## 終了コード

| コード | 意味 |
|---|---|
| 0 | 正常 |
| 1 | 対象が見つからない／複数に一致して確定できない |
| 2 | 引数エラー |
| 3 | 環境エラー（esptool が無い、設定ファイルが壊れている など） |

## 開発

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest
```

開発用の `.venv` にはチェックアウト先の絶対パスが記録される。リポジトリのディレクトリを
移動または改名した場合は、古い `.venv` を削除または退避して上記の手順で作り直す。

テストは実機を必要としない（`serial.tools.list_ports.comports` と `subprocess.run` を差し替える）。
`tests/conftest.py` のポート定義は実機 ESP32 から採取した値をもとにしている。
