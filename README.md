# rocmperf

A small **system monitor** with a [flet](https://flet.dev) GUI. It draws
GNOME-System-Monitor-style line charts and updates them every 0.5 seconds.

日本語版は [READMEJ.md](READMEJ.md) を参照してください.

## Monitored metrics

| Panel  | Source                          | Notes                          |
|--------|---------------------------------|--------------------------------|
| CPU    | `/proc/stat`                    | Aggregate CPU usage (%)        |
| Memory | `/proc/meminfo`                 | Used / total (GiB) + usage (%) |
| GPU    | `rocm-smi --showuse`            | AMD GPU utilization (%)        |
| VRAM   | `rocm-smi --showmeminfo vram`   | Used / total (GiB) + usage (%) |

Each panel keeps the last **120 samples (~60 seconds)** of history.
If `rocm-smi` is not available, the GPU and VRAM panels simply show `N/A`.

## Requirements

- Python 3
- [`flet`](https://pypi.org/project/flet/) (`pip install flet`)
- `rocm-smi` (optional — required only for GPU / VRAM readings)

## Usage

```bash
python3 sysmon.py
```

Only a single instance can run at a time (guarded by an `flock` on a lock file
under `$XDG_RUNTIME_DIR`). Launching a second copy exits immediately.

## Install to the GNOME / Ubuntu dock

The launcher and icon are installed into the per-user XDG directories, then the
app is pinned to the dock via `gsettings`.

```bash
# 1. Install the icon into the hicolor theme
install -Dm644 rocmperf-sysmon.svg \
  ~/.local/share/icons/hicolor/scalable/apps/rocmperf-sysmon.svg

# 2. Create the launcher (adjust the paths to your checkout)
cat > ~/.local/share/applications/rocmperf-sysmon.desktop <<'EOF'
[Desktop Entry]
Type=Application
Version=1.0
Name=System Monitor
Comment=Real-time CPU / memory / GPU / VRAM monitor (rocm-smi)
Exec=python3 /home/test/rocmperf/sysmon.py
Path=/home/test/rocmperf
Icon=rocmperf-sysmon
Terminal=false
Categories=System;Monitor;Utility;
StartupWMClass=System Monitor
EOF

# 3. Refresh caches
update-desktop-database ~/.local/share/applications
gtk-update-icon-cache -f -t ~/.local/share/icons/hicolor

# 4. Pin to the dock (append to the favorites list)
current=$(gsettings get org.gnome.shell favorite-apps)
gsettings set org.gnome.shell favorite-apps \
  "$(echo "$current" | sed "s/]$/, 'rocmperf-sysmon.desktop']/")"
```

If the icon does not appear right away, restart GNOME Shell (`Alt+F2` → `r` on
Xorg) or log out and back in (Wayland). To unpin it later, right-click the icon
and choose **Remove from Favorites**.

## Configuration

A few constants at the top of `sysmon.py` can be tuned:

| Constant          | Default | Meaning                              |
|-------------------|---------|--------------------------------------|
| `UPDATE_INTERVAL` | `0.5`   | Refresh interval in seconds          |
| `HISTORY`         | `120`   | Number of samples kept per chart     |
| `CHART_H`         | `96`    | Chart drawing height in pixels       |

## License

See [LICENSE](LICENSE).
