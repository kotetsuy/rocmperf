#!/usr/bin/env python3
"""rocm-smi を内部で呼び出す小さなシステムモニタ (flet GUI)。

監視対象: CPU / システムメモリ / GPU 使用率 / VRAM 使用率
GNOME システムモニタ風の折れ線グラフを 0.5 秒ごとに更新する。
"""

import asyncio
import fcntl
import json
import os
import shutil
import sys
import time
from collections import deque

import flet as ft
import flet.canvas as cv

_lock_handle = None            # プロセス生存中ロックを保持


def acquire_single_instance_lock():
    """多重起動を防ぐ。既に起動中なら False を返す。"""
    global _lock_handle
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    _lock_handle = open(os.path.join(runtime, "rocm-sysmon.lock"), "w")
    try:
        fcntl.flock(_lock_handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return False
    return True


UPDATE_INTERVAL = 0.5          # 秒
HISTORY = 120                  # 保持する点数 (120 点 = 60 秒)
CHART_H = 96                   # グラフ描画領域の高さ (px)
PAD_R = 40                     # 右側の % ラベル用余白


def _read_cpu_times():
    """/proc/stat の集計 CPU 行から (idle, total) を返す。"""
    with open("/proc/stat") as f:
        parts = f.readline().split()
    vals = list(map(int, parts[1:]))
    idle = vals[3] + (vals[4] if len(vals) > 4 else 0)  # idle + iowait
    return idle, sum(vals)


def _read_mem_percent():
    """システムメモリ使用率 (%) を /proc/meminfo から返す。"""
    info = {}
    with open("/proc/meminfo") as f:
        for line in f:
            k, _, v = line.partition(":")
            info[k] = int(v.split()[0])  # kB
    total = info.get("MemTotal", 1)
    avail = info.get("MemAvailable", info.get("MemFree", 0))
    used_kb = total - avail
    return used_kb / total * 100.0, used_kb / 1024 / 1024, total / 1024 / 1024  # %, GiB, GiB


async def _read_rocm():
    """rocm-smi を 1 回呼び出して (gpu%, vram%, vram_used_GiB, vram_total_GiB) を返す。"""
    try:
        proc = await asyncio.create_subprocess_exec(
            "rocm-smi", "--showuse", "--showmeminfo", "vram", "--json",
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
        )
        out, _ = await proc.communicate()
        data = json.loads(out.decode() or "{}")
    except Exception:
        return None
    if not data:
        return None
    # 最初のカードを使用
    card = next(iter(data.values()))

    def _num(key, default=0.0):
        try:
            return float(card.get(key, default))
        except (ValueError, TypeError):
            return default

    gpu = _num("GPU use (%)")
    vtot = _num("VRAM Total Memory (B)", 1.0) or 1.0
    vused = _num("VRAM Total Used Memory (B)")
    vram_pct = vused / vtot * 100.0
    return gpu, vram_pct, vused / 1024**3, vtot / 1024**3


def _find_xrt_smi():
    """xrt-smi の実行パスを返す。見つからなければ None。

    dock ランチャ起動時は PATH に XRT の bin が含まれないため、
    標準インストール先 /opt/xilinx/xrt/bin も探索する。"""
    exe = shutil.which("xrt-smi")
    if exe:
        return exe
    fallback = "/opt/xilinx/xrt/bin/xrt-smi"
    return fallback if os.path.exists(fallback) else None


async def _read_npu():
    """xrt-smi を呼び出して NPU 使用率を返す。

    XRT には rocm-smi のような「使用率 %」フィールドが無いため、
    アクティブな AIE パーティションが占有する列数 / 全列数 を使用率とみなす。
    返り値: (npu%, used_cols, total_cols) / 未インストール・失敗時は None。"""
    exe = _find_xrt_smi()
    if exe is None:
        return None

    # XRT のライブラリパスを補う (setup.sh 非 source でも動くように)
    env = dict(os.environ)
    xrt_root = os.path.dirname(os.path.dirname(exe))  # .../xilinx/xrt
    env["XILINX_XRT"] = xrt_root
    env["LD_LIBRARY_PATH"] = os.path.join(xrt_root, "lib") + os.pathsep + env.get("LD_LIBRARY_PATH", "")

    # xrt-smi は JSON を stdout に流せず -o のファイルにのみ出力する
    runtime = os.environ.get("XDG_RUNTIME_DIR", "/tmp")
    out_file = os.path.join(runtime, "rocm-sysmon-npu.json")
    try:
        proc = await asyncio.create_subprocess_exec(
            exe, "examine", "-r", "all", "-f", "JSON", "-o", out_file, "--force",
            env=env,
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.communicate()
        with open(out_file) as f:
            data = json.load(f)
        dev = data["devices"][0]
    except Exception:
        return None

    def _int(v, default=0):
        try:
            return int(v)
        except (ValueError, TypeError):
            return default

    total_cols = 0
    for plat in dev.get("platforms", []):
        total_cols = _int(plat.get("static_region", {}).get("total_columns"))
        if total_cols:
            break
    if not total_cols:
        total_cols = 8  # Strix Halo は 8 列

    # partitions はアイドル時 "" (空文字)、稼働中はパーティションのリスト
    used_cols = 0
    parts = dev.get("aie_partitions", {}).get("partitions", "")
    if isinstance(parts, list):
        for p in parts:
            if p.get("hw_contexts"):  # hw_context を持つ = 割り当て稼働中
                used_cols += _int(p.get("num_cols"))

    pct = min(100.0, used_cols / total_cols * 100.0) if total_cols else 0.0
    return pct, used_cols, total_cols


class Graph:
    """1 本の折れ線グラフパネル (タイトル + 現在値 + Canvas)。"""

    def __init__(self, title, color, unit_fn=None):
        self.title = title
        self.color = color
        self.unit_fn = unit_fn                 # 補助テキスト (例: "3.2 / 32 GiB")
        self.history = deque([0.0] * HISTORY, maxlen=HISTORY)
        self.w = 360                            # on_resize で更新
        self.value_text = ft.Text("0.0 %", size=13, weight=ft.FontWeight.BOLD, color=color)
        self.sub_text = ft.Text("", size=11, color=ft.Colors.ON_SURFACE_VARIANT)
        self.canvas = cv.Canvas(shapes=[], expand=True, height=CHART_H,
                                on_resize=self._on_resize, resize_interval=100)

    def _on_resize(self, e):
        self.w = max(1, e.width)
        self._redraw()

    def build(self):
        header = ft.Row(
            [ft.Text(self.title, size=13, weight=ft.FontWeight.W_600),
             ft.Row([self.sub_text, self.value_text], spacing=10)],
            alignment=ft.MainAxisAlignment.SPACE_BETWEEN,
        )
        return ft.Container(
            ft.Column([header, self.canvas], spacing=4),
            padding=ft.Padding(12, 8, 12, 8),
            border_radius=8,
            bgcolor=ft.Colors.SURFACE_CONTAINER_HIGHEST,
            margin=ft.Margin(0, 0, 0, 8),
        )

    def push(self, value, value_text, sub_text=""):
        self.history.append(max(0.0, min(100.0, value)))
        self.value_text.value = value_text
        self.sub_text.value = sub_text
        # ラベルはグラフ (canvas) の兄弟なので、canvas.update() では反映されない。
        # 個別に update() しないと初回の値のまま固まってしまう。
        if self.value_text.page:
            self.value_text.update()
        if self.sub_text.page:
            self.sub_text.update()
        self._redraw()

    def _redraw(self):
        w = self.w
        gw = max(1.0, w - PAD_R)            # 折れ線の描画幅
        h = CHART_H
        grid = ft.Paint(color=ft.Colors.OUTLINE_VARIANT, stroke_width=1,
                        style=ft.PaintingStyle.STROKE)
        faint = ft.Colors.with_opacity(0.55, ft.Colors.ON_SURFACE_VARIANT)
        shapes = [cv.Rect(0, 0, w, h, paint=ft.Paint(
            color=ft.Colors.with_opacity(0.4, ft.Colors.SURFACE), style=ft.PaintingStyle.FILL))]

        # 目盛り (0/20/40/60/80/100%)
        for pct in range(0, 101, 20):
            y = h - (pct / 100.0) * h
            y = min(h - 0.5, max(0.5, y))
            shapes.append(cv.Line(0, y, gw, y, paint=grid))
            shapes.append(cv.Text(gw + 4, y - 7, f"{pct} %",
                                  style=ft.TextStyle(size=9, color=faint)))

        # 折れ線 + 塗りつぶし
        n = len(self.history)
        dx = gw / (HISTORY - 1)
        pts = [((HISTORY - 1 - (n - 1 - i)) * dx, h - (v / 100.0) * h)
               for i, v in enumerate(self.history)]

        line_elems = [cv.Path.MoveTo(pts[0][0], pts[0][1])]
        for x, y in pts[1:]:
            line_elems.append(cv.Path.LineTo(x, y))

        fill_elems = list(line_elems) + [
            cv.Path.LineTo(pts[-1][0], h), cv.Path.LineTo(pts[0][0], h), cv.Path.Close()]
        shapes.append(cv.Path(fill_elems, paint=ft.Paint(
            color=ft.Colors.with_opacity(0.15, self.color), style=ft.PaintingStyle.FILL)))
        shapes.append(cv.Path(line_elems, paint=ft.Paint(
            color=self.color, stroke_width=1.8, style=ft.PaintingStyle.STROKE)))

        self.canvas.shapes = shapes
        if self.canvas.page:
            self.canvas.update()


async def main(page: ft.Page):
    page.title = "System Monitor"
    page.window.width = 460
    page.window.height = 820
    page.padding = 12
    page.spacing = 0

    graphs = {
        "cpu": Graph("CPU", ft.Colors.GREEN),
        "mem": Graph("メモリ", ft.Colors.RED),
        "gpu": Graph("GPU", ft.Colors.BLUE),
        "vram": Graph("VRAM", ft.Colors.PURPLE),
        "npu": Graph("NPU", ft.Colors.ORANGE),
    }
    page.add(ft.Column([g.build() for g in graphs.values()],
                       scroll=ft.ScrollMode.AUTO, expand=True))

    prev_idle, prev_total = _read_cpu_times()
    await asyncio.sleep(UPDATE_INTERVAL)

    while True:
        t0 = time.monotonic()

        # CPU
        idle, total = _read_cpu_times()
        d_total = total - prev_total
        cpu = (1 - (idle - prev_idle) / d_total) * 100.0 if d_total > 0 else 0.0
        prev_idle, prev_total = idle, total
        graphs["cpu"].push(cpu, f"{cpu:.1f} %")

        # メモリ
        mem_pct, used_g, total_g = _read_mem_percent()
        graphs["mem"].push(mem_pct, f"{mem_pct:.1f} %", f"{used_g:.1f} / {total_g:.1f} GiB")

        # GPU / VRAM (rocm-smi)
        rocm = await _read_rocm()
        if rocm is None:
            graphs["gpu"].push(0.0, "N/A")
            graphs["vram"].push(0.0, "N/A")
        else:
            gpu, vram_pct, vused_g, vtot_g = rocm
            graphs["gpu"].push(gpu, f"{gpu:.1f} %")
            graphs["vram"].push(vram_pct, f"{vram_pct:.1f} %", f"{vused_g:.1f} / {vtot_g:.1f} GiB")

        # NPU (xrt-smi)。未インストール時は graph=0 / text=N/A
        npu = await _read_npu()
        if npu is None:
            graphs["npu"].push(0.0, "N/A")
        else:
            npu_pct, used_c, total_c = npu
            graphs["npu"].push(npu_pct, f"{npu_pct:.1f} %", f"{used_c} / {total_c} cols")

        await asyncio.sleep(max(0.0, UPDATE_INTERVAL - (time.monotonic() - t0)))


if __name__ == "__main__":
    if not acquire_single_instance_lock():
        print("System Monitor は既に起動しています。", file=sys.stderr)
        sys.exit(0)
    ft.run(main)
