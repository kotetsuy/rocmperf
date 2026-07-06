# rocmperf

[flet](https://flet.dev) 製 GUI の小さな**システムモニタ**です。GNOME システム
モニタ風の折れ線グラフを描画し、0.5 秒ごとに更新します。

English version: see [README.md](README.md).

## 監視項目

| パネル | 取得元                          | 内容                              |
|--------|---------------------------------|-----------------------------------|
| CPU    | `/proc/stat`                    | CPU 全体の使用率 (%)              |
| メモリ | `/proc/meminfo`                 | 使用量 / 総量 (GiB) と使用率 (%)  |
| GPU    | `rocm-smi --showuse`            | AMD GPU 使用率 (%)               |
| VRAM   | `rocm-smi --showmeminfo vram`   | 使用量 / 総量 (GiB) と使用率 (%)  |

各パネルは直近 **120 サンプル（約 60 秒）** の履歴を保持します。
`rocm-smi` が使えない環境では、GPU / VRAM パネルは `N/A` と表示されます。

## 必要環境

- Python 3
- [`flet`](https://pypi.org/project/flet/)（`pip install flet`）
- `rocm-smi`（任意 — GPU / VRAM の取得にのみ必要）

## 使い方

```bash
python3 sysmon.py
```

多重起動は防止されます（`$XDG_RUNTIME_DIR` 配下のロックファイルに対する `flock`
で制御）。2 つ目を起動しようとした場合は、すぐに終了します。

## GNOME / Ubuntu ドックへのインストール

ランチャーとアイコンをユーザー単位の XDG ディレクトリに配置し、`gsettings` で
ドックへピン留めします。

```bash
# 1. アイコンを hicolor テーマに配置
install -Dm644 rocmperf-sysmon.svg \
  ~/.local/share/icons/hicolor/scalable/apps/rocmperf-sysmon.svg

# 2. ランチャーを作成（パスは自分のチェックアウト先に合わせる）
cat > ~/.local/share/applications/rocmperf-sysmon.desktop <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=System Monitor
Comment=CPU / メモリ / GPU / VRAM をリアルタイムに監視 (rocm-smi)
Exec=python3 /home/test/rocmperf/sysmon.py
Path=/home/test/rocmperf
Icon=rocmperf-sysmon
Terminal=false
Categories=System;Monitor;Utility;
StartupWMClass=System Monitor
EOF

# 3. キャッシュを更新
update-desktop-database ~/.local/share/applications
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor

# 4. ドックへピン留め（お気に入り一覧に追記）
current=$(gsettings get org.gnome.shell favorite-apps)
gsettings set org.gnome.shell favorite-apps \
  "$(echo "$current" | sed "s/]$/, 'rocmperf-sysmon.desktop']/")"
```

アイコンがすぐに表示されない場合は、GNOME Shell を再起動（Xorg なら
`Alt+F2` → `r`）するか、ログインし直してください（Wayland）。後で外すには、
アイコンを右クリックして**「お気に入りから削除」**を選びます。

## 設定

`sysmon.py` 冒頭の定数を変更して調整できます。

| 定数              | 既定値 | 意味                            |
|-------------------|--------|---------------------------------|
| `UPDATE_INTERVAL` | `0.5`  | 更新間隔（秒）                  |
| `HISTORY`         | `120`  | グラフごとに保持するサンプル数  |
| `CHART_H`         | `96`   | グラフ描画領域の高さ（px）      |

## ライセンス

[LICENSE](LICENSE) を参照してください。
