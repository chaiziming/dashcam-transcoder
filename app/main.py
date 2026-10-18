"""记录仪视频转码工具 —— 图形界面。

交互主线：拖入 .h264 → 自动抽帧预览 → 确认 VIN 自动遮挡 / 手动拖框挡车牌 →
按需填时间段 → 点「开始处理」→ 完成后一键打开。
"""
from __future__ import annotations

import os
import subprocess
import queue
import sys
import threading
import time
import traceback
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

# 拖放支持：优先用 tkinterdnd2（真正支持把文件拖进窗口），
# 缺失时自动退化为纯 Tk，程序仍可用，只是不能拖拽。
try:
    from tkinterdnd2 import DND_FILES, TkinterDnD

    _HAS_DND = True
except ImportError:  # pragma: no cover - 仅在缺少依赖时触发
    DND_FILES = None
    TkinterDnD = None
    _HAS_DND = False

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import config, ffmpeg_env, packager
from app.mask import (EFFECT_LABELS, REF_H, REF_W, VIN_PRESET_BOX, MaskPlan,
                      MaskRegion, scale_box)
from app.player import FrameReader, ProxySpec, ensure_proxy
from app.player_bar import PlayerBar, hhmmss_ms
from app.progress_bar import SegmentedProgress

APP_TITLE = "记录仪视频转码工具"
VIDEO_EXTS = (".h264", ".265", ".hevc", ".avc", ".mp4", ".m4v", ".avi", ".mov", ".mkv")

BG = "#f4f6f8"
CARD = "#ffffff"
FG = "#1f2933"
MUTED = "#6b7785"
ACCENT = "#2f6feb"
DANGER = "#d64545"
OK_GREEN = "#1f9d55"
BORDER = "#d7dee7"


def hhmmss(sec: float | None) -> str:
    """秒 -> HH:MM:SS。"""
    if sec is None or sec < 0:
        return "--:--:--"
    sec = int(round(sec))
    return f"{sec // 3600:02d}:{(sec % 3600) // 60:02d}:{sec % 60:02d}"


def parse_hhmmss(text: str) -> float | None:
    """解析 HH:MM:SS / MM:SS / 纯秒数。空串返回 None（表示不限制）。"""
    text = (text or "").strip()
    if not text:
        return None
    parts = text.split(":")
    try:
        nums = [float(p) for p in parts]
    except ValueError:
        raise ValueError(f"时间格式错误：{text}（应为 HH:MM:SS）")
    if len(nums) > 3:
        raise ValueError(f"时间格式错误：{text}")
    total = 0.0
    for n in nums:
        total = total * 60 + n
    return total


class MaskCanvas(tk.Frame):
    """预览画面 + 拖框遮挡。

    画面按比例缩放显示，内部坐标（视频像素）与显示坐标之间做双向换算。

    这里刻意继承 tk.Frame 而非 ttk.Frame：需要 -bg / -highlightbackground
    这类 tkinter 专有选项，ttk.Frame 不支持（会抛 TclError）。
    """

    def __init__(self, master, app: "MosaicApp"):
        super().__init__(master, bg=CARD, highlightbackground=BORDER, highlightthickness=1)
        self.app = app
        self.image: tk.PhotoImage | None = None
        self.img_w = 0
        self.img_h = 0
        self.scale = 1.0
        self.offset_x = 0
        self.offset_y = 0

        self.regions: list[MaskRegion] = []
        self._drag_from: tuple[float, float] | None = None
        self._drag_now: tuple[float, float] | None = None
        self._active_idx: int | None = None
        self._drop_hint = False
        # 关键：显示用的图可能是 640x360 的代理，但遮挡坐标必须记在原视频分辨率
        # （如 1920x1080）上，否则导出时坐标全错。
        self.video_w = 0
        self.video_h = 0

        # 光标说明：
        # 有预览图 -> crosshair（框选）；无图 -> hand2（提示去选文件）。
        # 之前固定 crosshair，在空白处按住拖会像"禁止"一样没反应。
        self.canvas = tk.Canvas(self, bg="#1a1d21", highlightthickness=0,
                                height=340, cursor="crosshair")
        self.canvas.pack(fill="both", expand=True, padx=8, pady=8)

        self.canvas.bind("<Configure>", lambda e: self._redraw())
        self.canvas.bind("<Button-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Button-3>", self._on_right_click)

    # ---------- 图像装载 ----------
    def load_image(self, path: Path) -> None:
        self.image = tk.PhotoImage(file=str(path))
        self.img_w = self.image.width()
        self.img_h = self.image.height()
        self.canvas.config(cursor="crosshair")
        self._redraw()

    def clear_image(self) -> None:
        self.image = None
        self.img_w = self.img_h = 0
        self.regions = []
        # 没图时给出明确可点的光标，而不是让用户以为坏了
        self.canvas.config(cursor="hand2")
        self._redraw()

    def set_frame_image(self, img, w: int, h: int) -> None:
        """显示一帧（播放用）。坐标基准不变，仍是原视频分辨率。"""
        self.image = img
        self.img_w, self.img_h = w, h
        self.canvas.config(cursor="crosshair")
        self._redraw()

    def set_video_size(self, w: int, h: int) -> None:
        self.video_w, self.video_h = w, h
        self._redraw()

    def set_drop_highlight(self, on: bool) -> None:
        """拖拽悬停时高亮边框，给出"松手就能放进来"的视觉反馈。"""
        if self._drop_hint == on:
            return
        self._drop_hint = on
        self.configure(highlightbackground=ACCENT if on else BORDER,
                       highlightthickness=2 if on else 1)
        self._redraw()

    def _fit(self) -> None:
        """计算缩放比例与居中偏移。"""
        cw = max(1, self.canvas.winfo_width())
        ch = max(1, self.canvas.winfo_height())
        if not self.img_w or not self.img_h:
            self.scale, self.offset_x, self.offset_y = 1.0, 0, 0
            return
        self.scale = min(cw / self.img_w, ch / self.img_h)
        self.offset_x = (cw - self.img_w * self.scale) / 2
        self.offset_y = (ch - self.img_h * self.scale) / 2

    def _to_display(self, x: float, y: float) -> tuple[float, float]:
        return self.offset_x + x * self.scale, self.offset_y + y * self.scale

    def _to_video(self, dx: float, dy: float) -> tuple[int, int]:
        """显示坐标 -> 视频真实像素坐标。

        画布上的图可能是代理分辨率（640x360），而遮挡必须记在原视频分辨率
        （1920x1080），所以这里要乘上比例。
        """
        ix = (dx - self.offset_x) / self.scale
        iy = (dy - self.offset_y) / self.scale
        if self.video_w and self.img_w:
            ix *= self.video_w / self.img_w
            iy *= self.video_h / self.img_h
        return max(0, int(ix)), max(0, int(iy))

    def _video_to_display_len(self, vx: float, vy: float) -> tuple[float, float]:
        """视频坐标长度 -> 显示长度。"""
        if self.video_w and self.img_w:
            return vx * (self.img_w / self.video_w), vy * (self.img_h / self.video_h)
        return vx, vy

    # ---------- 绘制 ----------
    def _redraw(self) -> None:
        self.canvas.delete("all")
        self._fit()
        if not self.image:
            self.canvas.create_text(
                self.canvas.winfo_width() / 2, self.canvas.winfo_height() / 2,
                text="把 .h264 文件拖到这里\n或点左上角「选择视频文件…」",
                fill="#9aa5b4", font=("Microsoft YaHei UI", 11), justify="center")
            return

        x0, y0 = self._to_display(0, 0)
        x1, y1 = self._to_display(self.img_w, self.img_h)
        self.canvas.create_image(x0, y0, image=self.image,
                                 anchor="nw", tags="frame")

        for r in self.regions:
            self._draw_region(r, label=r.label)

        # 正在拖拽的临时框
        if self._drag_from and self._drag_now:
            ax, ay = self._drag_from
            bx, by = self._drag_now
            rx0, ry0 = self._to_display(min(ax, bx), min(ay, by))
            rx1, ry1 = self._to_display(max(ax, bx), max(ay, by))
            self.canvas.create_rectangle(
                rx0, ry0, rx1, ry1, outline="#ffd166", width=2, dash=(4, 3))

        # 选中框高亮描边
        if self._active_idx is not None and self._active_idx < len(self.regions):
            r = self.regions[self._active_idx]
            dx, dy = self._video_to_display_len(r.x, r.y)
            dw, dh = self._video_to_display_len(r.w, r.h)
            ax, ay = self._to_display(dx, dy)
            bx, by = self._to_display(dx + dw, dy + dh)
            self.canvas.create_rectangle(ax - 2, ay - 2, bx + 2, by + 2,
                                         outline="#ffd166", width=2)

    def _draw_region(self, r: MaskRegion, label: str) -> None:
        # r 的坐标是视频真实像素，画在代理图上要按比例缩放
        dx, dy = self._video_to_display_len(r.x, r.y)
        dw, dh = self._video_to_display_len(r.w, r.h)
        ax, ay = self._to_display(dx, dy)
        bx, by = self._to_display(dx + dw, dy + dh)
        # 半透明黑 + 描边，直观表示「这里会被纯色盖住」
        self.canvas.create_rectangle(ax, ay, bx, by, fill="#000000",
                                     stipple="gray50", outline="#ffd166", width=1)
        if bx - ax > 40:
            self.canvas.create_text(ax + 5, ay - 8, text=label, anchor="w",
                                    fill="#ffd166", font=("Microsoft YaHei UI", 8, "bold"))

    # ---------- 鼠标交互 ----------
    def _on_press(self, event) -> None:
        if not self.image:
            return
        # 点在已有框上则选中，否则开始新框
        for i, r in enumerate(self.regions):
            dx, dy = self._video_to_display_len(r.x, r.y)
            dw, dh = self._video_to_display_len(r.w, r.h)
            ax, ay = self._to_display(dx, dy)
            bx, by = self._to_display(dx + dw, dy + dh)
            if ax <= event.x <= bx and ay <= event.y <= by:
                self._active_idx = i
                self._drag_from = self._to_video(event.x, event.y)
                self._drag_now = self._drag_from
                self._redraw()
                return
        self._active_idx = None
        self._drag_from = self._to_video(event.x, event.y)
        self._drag_now = self._drag_from
        self._redraw()

    def _on_drag(self, event) -> None:
        if self._drag_from is None:
            return
        self._drag_now = self._to_video(event.x, event.y)
        self._redraw()

    def _on_release(self, event) -> None:
        if self._drag_from is None:
            return
        start = self._drag_from
        end = self._to_video(event.x, event.y)
        self._drag_from = self._drag_now = None

        x0, x1 = sorted((start[0], end[0]))
        y0, y1 = sorted((start[1], end[1]))
        # 夹到视频真实范围内，避免因代理/原图分辨率差异产生越界坐标
        if self.video_w:
            x0, x1 = max(0, min(x0, self.video_w - 1)), max(0, min(x1, self.video_w))
            y0, y1 = max(0, min(y0, self.video_h - 1)), max(0, min(y1, self.video_h))
        # 小于 4 像素视为误触，忽略
        if x1 - x0 < 4 or y1 - y0 < 4:
            self._redraw()
            return

        # 已选中已有框 -> 视为移动/缩放该框
        if self._active_idx is not None and self._active_idx < len(self.regions):
            r = self.regions[self._active_idx]
            lim_w = self.video_w or self.img_w
            lim_h = self.video_h or self.img_h
            nx = max(0, min(r.x + (x0 - start[0]), lim_w - r.w))
            ny = max(0, min(r.y + (y0 - start[1]), lim_h - r.h))
            r.x, r.y = nx, ny
            self._active_idx = None
            self._redraw()
            self.app.on_regions_changed()
            return

        self.regions.append(MaskRegion(x0, y0, x1 - x0, y1 - y0, label="遮挡"))
        self._redraw()
        self.app.on_regions_changed()

    def _on_right_click(self, event) -> None:
        """右键删除点击到的框。"""
        for i, r in enumerate(self.regions):
            dx, dy = self._video_to_display_len(r.x, r.y)
            dw, dh = self._video_to_display_len(r.w, r.h)
            ax, ay = self._to_display(dx, dy)
            bx, by = self._to_display(dx + dw, dy + dh)
            if ax <= event.x <= bx and ay <= event.y <= by:
                self.regions.pop(i)
                self._redraw()
                self.app.on_regions_changed()
                return


class MosaicApp(TkinterDnD.Tk if _HAS_DND else tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title(APP_TITLE)
        self.configure(bg=BG)
        self.geometry("1180x820")
        self.minsize(1020, 700)

        self.cfg = config.load()
        self.ffmpeg = ffmpeg_env.find_binary("ffmpeg")
        self.ffprobe = ffmpeg_env.find_binary("ffprobe")

        self.src: Path | None = None
        self.info: ffmpeg_env.VideoInfo | None = None
        self.preview_png: Path | None = None
        self.vin_box_real: tuple[int, int, int, int] = VIN_PRESET_BOX
        self.worker: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.busy = False

        # ---- 预览播放 ----
        self.proxy: Path | None = None          # 代理预览文件
        self.reader: FrameReader | None = None  # 帧读取器
        self.playing = False
        self.position = 0.0                    # 当前播放位置（秒）
        self.speed = 1.0
        self.play_fps = 15.0
        self._play_job: str | None = None
        self._seek_lock = threading.Lock()     # 换源/seek 时防止并发
        self._ready_for_seek = threading.Event()
        self._seeking = False
        # 后台线程 -> 主线程的消息队列。
        # 不用「后台线程直接 self.after」：tkinter 不是线程安全的，
        # 那样做要求主线程必须正处于 mainloop，测试/无头环境下会抛
        # RuntimeError: main thread is not in main loop。
        self._ui_queue: "queue.Queue[tuple[str, tuple]]" = queue.Queue()
        self._poll_job: str | None = None
        self._reading = False        # 是否正在后台读帧，避免并发读同一管道
        self._resume_after_seek = False  # seek 的首帧读完后才恢复播放循环

        self._build_style()
        self._build_ui()
        self._check_ffmpeg()
        self._register_dnd()
        self._bind_player_keys()
        self._start_ui_polling()
        self._apply_idle_state()

    # ================= 界面 =================
    def _build_style(self) -> None:
        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD, relief="flat")
        style.configure("TLabel", background=BG, foreground=FG,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background=CARD, foreground=FG,
                        font=("Microsoft YaHei UI", 9))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED,
                        font=("Microsoft YaHei UI", 8))
        style.configure("Title.TLabel", background=BG, foreground=FG,
                        font=("Microsoft YaHei UI", 15, "bold"))
        style.configure("Section.TLabel", background=CARD, foreground=FG,
                        font=("Microsoft YaHei UI", 10, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 9), padding=(10, 6))
        style.configure("Accent.TButton", background=ACCENT, foreground="#ffffff",
                        font=("Microsoft YaHei UI", 10, "bold"), padding=(14, 9))
        style.map("Accent.TButton",
                  background=[("active", "#2559c4"), ("disabled", "#9db8e8")])
        style.configure("Danger.TButton", background=DANGER, foreground="#ffffff",
                        padding=(10, 6))
        style.configure("TCheckbutton", background=CARD, foreground=FG,
                        font=("Microsoft YaHei UI", 9))
        style.configure("TEntry", font=("Microsoft YaHei UI", 9))
        style.configure("TCombobox", font=("Microsoft YaHei UI", 9))
        style.configure("Horizontal.TProgressbar", background=ACCENT,
                        troughcolor="#e3e8ef", borderwidth=0)

    def _build_ui(self) -> None:
        self.columnconfigure(0, weight=1)
        self.rowconfigure(1, weight=1)

        self._build_header()
        body = ttk.Frame(self, padding=(12, 0, 12, 0))
        body.grid(row=1, column=0, sticky="nsew")
        body.columnconfigure(0, weight=3, uniform="main")
        body.columnconfigure(1, weight=2, uniform="main")
        body.rowconfigure(0, weight=1)

        self._build_left(body)
        self._build_right(body)
        self._build_footer()
        self.on_regions_changed()

    def _build_header(self) -> None:
        head = ttk.Frame(self, padding=(14, 10, 14, 8))
        head.grid(row=0, column=0, sticky="ew")
        head.columnconfigure(1, weight=1)
        ttk.Label(head, text="记录仪视频转码工具", style="Title.TLabel").grid(
            row=0, column=0, sticky="w")
        self.ffmpeg_status = ttk.Label(head, text="", style="Muted.TLabel")
        self.ffmpeg_status.grid(row=0, column=1, sticky="e", padx=(10, 0))
        ttk.Label(head,
                  text="把行车记录仪的 .h264 转成可播放的 mp4，并遮挡 VIN 等隐私信息",
                  style="Muted.TLabel").grid(row=1, column=0, columnspan=2, sticky="w")

    def _build_left(self, parent) -> None:
        card = ttk.Frame(parent, style="Card.TFrame", padding=12)
        card.grid(row=0, column=0, sticky="nsew", padx=(0, 6))
        card.columnconfigure(0, weight=1)
        # 画布在 row=1，只有它需要吃掉多余高度；顶部操作条 row=0、
        # 播放控制条 row=2 各占一行，都不该被拉伸。
        card.rowconfigure(1, weight=1)

        # --- 顶部操作条 ---
        top = ttk.Frame(card, style="Card.TFrame")
        top.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        top.columnconfigure(1, weight=1)
        self.btn_choose = ttk.Button(top, text="添加预览",
                                     command=self.on_add_preview)
        self.btn_choose.grid(row=0, column=0, sticky="w")

        # 合并多个分段视频（行车记录仪按时间分段录制，同一起事故常分散在两个文件里）
        self.btn_merge = ttk.Button(top, text="合并", command=self.on_merge)
        self.btn_merge.grid(row=0, column=2, sticky="e")
        self._sync_merge_btn()

# 已添加的预览列表（合并顺序 = 添加顺序）。
        # 上限 4 个：再多的分段极少见，且列表太长会让界面变啰嗦。
        self.preview_files: list[Path] = []
        self.MAX_PREVIEW = 4

        # 原来的「换帧预览」按钮已移除：进度条拖拽 + 快进快退已能覆盖同样的需求，
        # 而且它原本直接解码原始裸流，点了会很慢。
        self.lbl_src = ttk.Label(top, text="未选择文件", style="Muted.TLabel",
                                 anchor="w")
        self.lbl_src.grid(row=0, column=1, sticky="ew", padx=(10, 6))

        # --- 预览画布 ---
        # 控制条放在画布**下方**（主流播放器都是控制条在下），
        # 所以画布占 row=1、控制条占 row=2。
        self.canvas_box = MaskCanvas(card, self)
        self.canvas_box.grid(row=1, column=0, sticky="nsew")

        # --- 播放控制条（位于视频下方）---
        self.player_bar = PlayerBar(card, self)
        self.player_bar.grid(row=2, column=0, sticky="ew", pady=(4, 0))

        self.lbl_hint = ttk.Label(
            card,
            text="提示：在画面上按住左键拖动即可框出遮挡区；"
                 "右键点框删除；点框后再拖动可调整位置。",
            style="Muted.TLabel")
        self.lbl_hint.grid(row=3, column=0, sticky="w", pady=(6, 0))

    def _build_right(self, parent) -> None:
        side = ttk.Frame(parent, style="Card.TFrame", padding=12)
        side.grid(row=0, column=1, sticky="nsew", padx=(6, 0))
        side.columnconfigure(0, weight=1)

        r = 0
        ttk.Label(side, text="文件信息", style="Section.TLabel").grid(
            row=r, column=0, sticky="w", pady=(0, 4))
        r += 1
        self.lbl_info = ttk.Label(side, text="—", style="Muted.TLabel",
                                  justify="left", wraplength=330)
        self.lbl_info.grid(row=r, column=0, sticky="w", pady=(0, 10))
        r += 1

        # 遮挡设置
        ttk.Separator(side).grid(row=r, column=0, sticky="ew", pady=(0, 10))
        r += 1
        ttk.Label(side, text="遮挡设置", style="Section.TLabel").grid(
            row=r, column=0, sticky="w", pady=(0, 6))
        r += 1

        self.var_vin = tk.BooleanVar(value=bool(self.cfg.get("use_vin_preset", True)))
        ttk.Checkbutton(side, text="自动遮挡 VIN（右下角固定位置）",
                        variable=self.var_vin,
                        command=self.on_vin_toggle).grid(row=r, column=0, sticky="w")
        r += 1
        self.lbl_vinbox = ttk.Label(side, text="", style="Muted.TLabel")
        self.lbl_vinbox.grid(row=r, column=0, sticky="w", pady=(2, 8))
        r += 1

        ttk.Label(side, text="遮挡区域列表：", style="Muted.TLabel").grid(
            row=r, column=0, sticky="w")
        r += 1
        self.lst_regions = tk.Listbox(side, height=6, font=("Consolas", 9),
                                     activestyle="none", highlightthickness=1,
                                     highlightbackground=BORDER, borderwidth=0,
                                     selectbackground="#dce8fb", selectforeground=FG)
        self.lst_regions.grid(row=r, column=0, sticky="ew", pady=(2, 4))
        r += 1
        btnrow = ttk.Frame(side, style="Card.TFrame")
        btnrow.grid(row=r, column=0, sticky="ew", pady=(0, 8))
        ttk.Button(btnrow, text="清空自定义框", command=self.on_clear_regions).pack(
            side="left")
        r += 1

        # 打码效果
        eff = ttk.Frame(side, style="Card.TFrame")
        eff.grid(row=r, column=0, sticky="ew", pady=(0, 8))
        eff.columnconfigure(1, weight=1)
        ttk.Label(eff, text="效果", style="Muted.TLabel").grid(
            row=0, column=0, sticky="w", padx=(0, 6))
        self.var_effect = tk.StringVar(
            value=EFFECT_LABELS.get(self.cfg.get("effect", "solid"), EFFECT_LABELS["solid"]))
        self.cmb_effect = ttk.Combobox(
            eff, textvariable=self.var_effect, state="readonly",
            values=list(EFFECT_LABELS.values()))
        self.cmb_effect.grid(row=0, column=1, sticky="ew")
        self.cmb_effect.bind("<<ComboboxSelected>>", lambda e: self.on_effect_change())
        r += 1

        # 强度滑条（不同效果的含义不同，随效果切换改文案与范围）
        strow = ttk.Frame(side, style="Card.TFrame")
        strow.grid(row=r, column=0, sticky="ew", pady=(0, 10))
        strow.columnconfigure(1, weight=1)
        self.lbl_strength = ttk.Label(strow, text="强度", style="Muted.TLabel")
        self.lbl_strength.grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.var_strength = tk.IntVar(value=int(self.cfg.get("intensity", 21)))
        self.sld_strength = ttk.Scale(
            strow, from_=1, to=60, orient="horizontal",
            variable=self.var_strength, command=self.on_strength_change)
        self.sld_strength.grid(row=0, column=1, sticky="ew")
        self.lbl_strength_val = ttk.Label(strow, text="", style="Muted.TLabel", width=5,
                                          anchor="e")
        self.lbl_strength_val.grid(row=0, column=2, sticky="e", padx=(6, 0))
        r += 1
        self.on_effect_change()

        # 时间段
        ttk.Separator(side).grid(row=r, column=0, sticky="ew", pady=(0, 10))
        r += 1
        ttk.Label(side, text="时间段（留空 = 整段）", style="Section.TLabel").grid(
            row=r, column=0, sticky="w", pady=(0, 6))
        r += 1
        grid = ttk.Frame(side, style="Card.TFrame")
        grid.grid(row=r, column=0, sticky="ew", pady=(0, 4))
        grid.columnconfigure(1, weight=1)
        grid.columnconfigure(3, weight=1)
        ttk.Label(grid, text="开始", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.var_start = tk.StringVar()
        self.ent_start = ttk.Entry(grid, textvariable=self.var_start, width=10)
        self.ent_start.grid(row=0, column=1, sticky="ew", padx=(4, 10))
        ttk.Label(grid, text="结束", style="Muted.TLabel").grid(row=0, column=2, sticky="w")
        self.var_end = tk.StringVar()
        self.ent_end = ttk.Entry(grid, textvariable=self.var_end, width=10)
        self.ent_end.grid(row=0, column=3, sticky="ew", padx=(4, 0))
        r += 1
        ttk.Label(side, text="格式 HH:MM:SS，例如 00:01:00 表示前 1 分钟",
                  style="Muted.TLabel").grid(row=r, column=0, sticky="w", pady=(0, 10))
        r += 1

        # 输出设置
        ttk.Separator(side).grid(row=r, column=0, sticky="ew", pady=(0, 10))
        r += 1
        ttk.Label(side, text="输出设置", style="Section.TLabel").grid(
            row=r, column=0, sticky="w", pady=(0, 6))
        r += 1

        out = ttk.Frame(side, style="Card.TFrame")
        out.grid(row=r, column=0, sticky="ew")
        out.columnconfigure(1, weight=1)
        ttk.Label(out, text="文件名", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.var_outname = tk.StringVar()
        self.ent_out = ttk.Entry(out, textvariable=self.var_outname)
        self.ent_out.grid(row=0, column=1, sticky="ew", padx=(6, 4))
        self.btn_pick_out = ttk.Button(out, text="…", width=3,
                                        command=self.on_pick_output)
        self.btn_pick_out.grid(row=0, column=2)
        r += 1

        q = ttk.Frame(side, style="Card.TFrame")
        q.grid(row=r, column=0, sticky="ew", pady=(8, 0))
        q.columnconfigure(1, weight=1)
        ttk.Label(q, text="画质", style="Muted.TLabel").grid(row=0, column=0, sticky="w")
        self.var_crf = tk.IntVar(value=int(self.cfg.get("crf", 21)))
        self.cmb_crf = ttk.Combobox(q, textvariable=self.var_crf, width=8, state="readonly",
                                    values=(18, 20, 21, 23, 25, 28))
        self.cmb_crf.grid(row=0, column=1, sticky="w", padx=(6, 0))
        self.btn_crf_help = ttk.Button(q, text="?", width=2, command=self.on_crf_help)
        self.btn_crf_help.grid(row=0, column=2, padx=(4, 0))
        r += 1
        ttk.Label(side, text="CRF 越小越清晰、体积越大；举报材料建议 21",
                  style="Muted.TLabel").grid(row=r, column=0, sticky="w", pady=(2, 0))
        r += 1

    def _build_footer(self) -> None:
        foot = ttk.Frame(self, padding=(12, 8, 12, 12))
        foot.grid(row=2, column=0, sticky="ew")
        foot.columnconfigure(1, weight=1)

        self.progress = SegmentedProgress(foot, segments=24, width=300, height=22)
        self.progress.grid(row=0, column=0, sticky="w", pady=(0, 0))
        # 初始文案留空：空闲状态下这个标签整个隐藏，由 _apply_idle_state 控制
        self.lbl_prog = ttk.Label(foot, text="", style="Muted.TLabel")
        self.lbl_prog.grid(row=0, column=1, sticky="w", padx=(10, 0))

        self.btn_start = ttk.Button(foot, text="开始处理", style="Accent.TButton",
                                    command=self.on_start, state="disabled")
        self.btn_start.grid(row=0, column=2, padx=(8, 6))
        self.btn_cancel = ttk.Button(foot, text="取消", style="Danger.TButton",
                                     command=self.on_cancel, state="disabled")
        self.btn_cancel.grid(row=0, column=3, padx=(0, 6))
        self.btn_open_dir = ttk.Button(foot, text="打开输出目录", command=self.on_open_dir,
                                        state="disabled")
        self.btn_open_dir.grid(row=0, column=4, padx=(0, 6))
        self.btn_play = ttk.Button(foot, text="播放结果", command=self.on_play,
                                   state="disabled")
        self.btn_play.grid(row=0, column=5)

        self.lbl_done = ttk.Label(foot, text="", style="Muted.TLabel")
        self.lbl_done.grid(row=1, column=0, columnspan=6, sticky="w", pady=(4, 0))

    # ============================================================
    #              主线程 <-> 后台线程 通信
    # ============================================================
    def _post(self, kind: str, *args) -> None:
        """后台线程投递 UI 更新（线程安全的入口）。"""
        self._ui_queue.put((kind, args))

    def _start_ui_polling(self) -> None:
        """在主线程定期排空队列。这是唯一允许改 UI 的地方。

        不能让后台线程直接调 self.after()：tkinter 不是线程安全的，
        那样做要求主线程必须正处于 mainloop，测试/无头环境下会抛
        RuntimeError: main thread is not in main loop。
        """
        def pump() -> None:
            try:
                while True:
                    kind, args = self._ui_queue.get_nowait()
                    self._dispatch_ui(kind, *args)
            except queue.Empty:
                pass
            except Exception:  # noqa: BLE001 - 单条消息失败不能拖垮主循环
                traceback.print_exc()
            self._poll_job = self.after(15, pump)

        self._poll_job = self.after(15, pump)

    def _dispatch_ui(self, kind: str, *args) -> None:
        handlers = {
            "proxy_progress": lambda: self._on_proxy_progress(*args),
            "proxy_ready": lambda: self._on_proxy_ready(*args),
            "job_progress": lambda: self._update_progress(*args),
            "job_log": lambda: self._notify(*args),
            "job_done": lambda: self._finish_job(*args),
            "play_frame": lambda: self._on_frame_ready(*args),
            "seek_frame": lambda: self._on_seek_frame_ready(*args),
            "merge_progress": lambda: self._on_merge_progress(*args),
            "merge_done": lambda: self._on_merge_done(*args),
        }
        fn = handlers.get(kind)
        if fn:
            fn()

    def _show_prog(self, text: str = "") -> None:
        """状态文字。有内容才显示，空则隐藏（与 _show_done_line 同一套规则）。"""
        if text:
            self.lbl_prog.config(text=text)
            self.lbl_prog.grid()
        else:
            self.lbl_prog.config(text="")
            self.lbl_prog.grid_remove()

    # ---------- 空闲状态：隐藏与"正在干活"相关的元素 ----------
    def _apply_idle_state(self) -> None:
        """没有任务时，隐藏进度条 / 状态文字 / 播放控制条。

        空界面不该出现"就绪""正在生成预览"这类占位文案 ——
        它们在没有任何任务时只会让人困惑（截图里就是这个问题）。
        """
        self.progress.grid_remove()
        self.lbl_prog.grid_remove()
        self.lbl_done.grid_remove()
        if not self.src:
            # 以下元素都属于"已载入视频"的语境，没文件时不出现：
            # 播放条、预览时间点输入、框选操作指引
            self.player_bar.grid_remove()
            self.lbl_hint.grid_remove()

    def _apply_busy_state(self) -> None:
        """有任务时，恢复进度条与状态文字。"""
        self.progress.grid()
        self.lbl_prog.grid()
        if self.src:
            self.player_bar.grid()
            self.lbl_hint.grid()

    def _show_done_line(self, text: str = "") -> None:
        """结果提示行：有内容才显示。"""
        if text:
            self.lbl_done.config(text=text)
            self.lbl_done.grid()
        else:
            self.lbl_done.grid_remove()

    def _check_ffmpeg(self) -> None:
        if self.ffmpeg and Path(self.ffmpeg).is_file() and self.ffprobe:
            self.ffmpeg_status.config(text=f"ffmpeg: {Path(self.ffmpeg).parent}",
                                      foreground=OK_GREEN)
        else:
            self.ffmpeg_status.config(text="未找到 ffmpeg / ffprobe", foreground=DANGER)
            self._notify("未找到 ffmpeg 或 ffprobe。\n\n"
                         "请把 ffmpeg.exe 和 ffprobe.exe 放到程序同级目录的 ffmpeg 文件夹下，"
                         "或确保它们在系统 PATH 中。")

    # ================= 事件处理 =================
    def _notify(self, msg: str) -> None:
        """底部提示行。传空串则整行隐藏（避免残留上一条消息）。"""
        text = msg.replace("\n", " ")
        if text:
            self._show_done_line(text)
        else:
            self.lbl_done.config(text="")
            self.lbl_done.grid_remove()

    def on_add_preview(self) -> None:
        """添加视频到预览列表（可多次添加，顺序即合并顺序）。"""
        start_dir = self.cfg.get("last_dir") or str(Path.home())
        paths = filedialog.askopenfilenames(
            title="选择要预览的视频（可多选）",
            initialdir=start_dir,
            filetypes=[("视频文件", " ".join(f"*{e}" for e in VIDEO_EXTS)),
                       ("h264 裸流", "*.h264"), ("所有文件", "*.*")])
        if not paths:
            return

        added, dup, full = 0, 0, 0
        for p in paths:
            f = Path(p)
            if f in self.preview_files:
                dup += 1
                continue
            if len(self.preview_files) >= self.MAX_PREVIEW:
                full += 1
                continue
            self.preview_files.append(f)
            added += 1

        msgs = []
        if added:
            msgs.append(f"已添加 {added} 个")
        if dup:
            msgs.append(f"{dup} 个重复已跳过")
        if full:
            msgs.append(f"{full} 个超出上限（最多 {self.MAX_PREVIEW} 个）")
        if msgs:
            self._notify("；".join(msgs))

        self._refresh_preview_bar()
        # 第一个视频自动载入主画面，省得再点一次
        if added and self.src is None:
            self.load_file(self.preview_files[0])

    def _refresh_preview_bar(self) -> None:
        """更新顶部的预览列表提示条。"""
        if not hasattr(self, "lbl_src"):
            return
        n = len(self.preview_files)
        if n == 0:
            self.lbl_src.config(text="未选择文件")
            return
        names = "、".join(f.name for f in self.preview_files[:3])
        more = f" 等 {n} 个" if n > 3 else ""
        prefix = f"预览 {n} 个："
        cur = f.name if getattr(self, "src", None) else ""
        self.lbl_src.config(text=f"{prefix}{names}{more}" + (f"   （当前：{cur}）" if cur else ""))

    def on_choose(self) -> None:
        """兼容旧入口：等价于添加单个视频。"""
        start_dir = self.cfg.get("last_dir") or str(Path.home())
        path = filedialog.askopenfilename(
            title="选择行车记录仪视频",
            initialdir=start_dir,
            filetypes=[("视频文件", " ".join(f"*{e}" for e in VIDEO_EXTS)),
                       ("h264 裸流", "*.h264"), ("所有文件", "*.*")])
        if path:
            f = Path(path)
            if f not in self.preview_files:
                self.preview_files.append(f)
            self._refresh_preview_bar()
            self.load_file(f)

    # ---------- 合并多个分段视频 ----------
    def _sync_merge_btn(self) -> None:
        """合并按钮的可用状态取决于 ffmpeg 是否就绪。"""
        if hasattr(self, "btn_merge"):
            self.btn_merge.config(
                state="normal" if self.ffmpeg and self.ffprobe else "disabled")

    def on_merge(self) -> None:
        """把「添加预览」里已添加的视频按顺序拼成一个，并应用当前遮挡设置。

        典型场景：行车记录仪按时间分段录制，同一起事故被切在两个文件里，
        合并后才能看清完整过程。

        不再弹框重新选文件 —— 直接用预览列表（顺序即合并顺序），
        避免重复选择时漏选、选错顺序。
        """
        if not self.ffmpeg:
            self._notify("ffmpeg 未就绪")
            return

        files = list(getattr(self, "preview_files", []))
        # 只合并还存在的文件
        files = [f for f in files if f.is_file()]
        if len(files) < 2:
            self._notify(
                f"当前只有 {len(files)} 个视频，无法合并。\n"
                "请先点「添加预览」添加 2 个以上（可分多次添加）。")
            return

        total_mb = sum(f.stat().st_size for f in files) / 1024 / 1024
        if not messagebox.askyesno(
                "确认合并",
                f"将按下面的顺序合并 {len(files)} 个视频：\n\n"
                + "\n".join(f"  {i+1}. {f.name}" for i, f in enumerate(files))
                + f"\n\n合并后约 {total_mb:.0f} MB"
                  "\n（输出为 MP4，已内置当前遮挡设置）\n\n是否继续？"):
            return

        default_name = f"{files[0].stem}_合并.mp4"
        out = filedialog.asksaveasfilename(
            title="保存合并结果",
            initialdir=str(files[0].parent),
            initialfile=default_name,
            defaultextension=".mp4",
            filetypes=[("MP4 视频", "*.mp4")])
        if not out:
            return

        # 复用当前界面的遮挡设置（与「开始处理」用同一份构造逻辑）
        src_info = self.info
        width = src_info.width if src_info else 1920
        height = src_info.height if src_info else 1080
        plan = MaskPlan(
            width=width, height=height,
            use_vin_preset=bool(self.var_vin.get()),
            vin_box=self.vin_box_real,
            extra=[MaskRegion(**r.to_dict()) for r in self.canvas_box.regions],
            effect=self._current_effect(),
            intensity=int(self.var_strength.get() or 0),
        )
        crf = int(self.var_crf.get())
        fps = src_info.fps if src_info and src_info.fps > 0 else 25.0

        try:
            cmd = packager.build_merge_command(
                self.ffmpeg, files, out, plan,
                crf=crf, preset="slow", fps=fps)
        except ValueError as e:
            self._notify(f"合并参数有误：{e}")
            return

        self._set_busy(True, f"正在合并 {len(files)} 个视频…")
        try:
            self._run_merge_job(cmd, out)
        finally:
            self._set_busy(False)

    def _run_merge_job(self, cmd: list[str], out: str) -> None:
        """在后台线程跑合并，完成后回主线程提示。"""
        def work() -> None:
            def on_progress(p: packager.Progress) -> None:
                self._post("merge_progress", p)
            def on_log(line: str) -> None:
                if any(k in line for k in ("Error", "error", "Invalid")):
                    self._post("job_log", line.strip())

            total = self._estimate_merge_seconds_for(cmd)
            res = packager.run_merge_job(
                cmd, out, total_duration=total,
                on_progress=on_progress, on_log=on_log,
                cancel_event=self.cancel_event)
            self._post("merge_done", res, out)

        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _estimate_merge_seconds_for(self, cmd: list[str]) -> float | None:
        """从已生成的命令里取出输入列表，估算总时长。"""
        files = []
        for i, a in enumerate(cmd):
            if a == "-i" and i + 1 < len(cmd):
                files.append(Path(cmd[i + 1]))
        return self._estimate_merge_seconds(files) if files else None

    def _estimate_merge_seconds(self, files: list[Path]) -> float | None:
        """估算合并后的总时长，用来算进度百分比。

        裸流 .h264 的 ffprobe 拿不到 duration（裸流没有容器头），
        需要退回数帧反推；任何一段算不出来就返回 None，
        让进度条改用「不确定时长」模式而不是显示错误百分比。
        """
        total = 0.0
        for f in files:
            info = ffmpeg_env.probe_video(f, self.ffprobe)
            if info is None:
                return None
            if info.duration is None:
                info.duration = ffmpeg_env.probe_duration_by_counting(
                    self.ffprobe, f, info.fps)
            if info.duration is None:
                return None
            total += info.duration
        return total or None

    def _on_merge_progress(self, p: "packager.Progress") -> None:
        if p.pct is not None:
            self.progress.set_progress(p.pct)
            self._show_prog(f"{p.pct:.0f}%  {hhmmss(p.seconds)}")
        else:
            self.progress.set_indeterminate(True)
            self._show_prog(f"已处理 {hhmmss(p.seconds)}  {p.stage}")

    def _on_merge_done(self, res: "packager.JobResult", out: str) -> None:
        self.progress.reset()
        self._set_busy(False)
        if not res.ok:
            self._notify(f"合并失败：{res.message}")
            messagebox.showerror("合并失败",
                                 f"{res.message}\n\n{res.stderr_tail[-400:]}")
            return
        self._notify(f"合并完成：{Path(out).name}")
        messagebox.showinfo(
            "合并完成",
            f"已按所选顺序合并为一个连续视频，并应用了当前遮挡设置。\n\n"
            f"保存位置：\n{out}")

    def on_clear_regions(self) -> None:
        """清空全部自定义遮挡框（不影响自动 VIN 遮挡）。"""
        self.canvas_box.regions = []
        self.canvas_box._redraw()
        self.on_regions_changed()

    def on_regions_changed(self) -> None:
        """刷新遮挡区域列表，让用户导出前能核对坐标。"""
        self.lst_regions.delete(0, "end")
        if self.var_vin.get():
            x, y, w, h = self.vin_box_real
            self.lst_regions.insert("end", f"[自动] VIN      x={x} y={y} w={w} h={h}")
        for i, r in enumerate(self.canvas_box.regions, 1):
            self.lst_regions.insert(
                "end", f"[自定义{i}] x={r.x} y={r.y} w={r.w} h={r.h}")

    def on_pick_output(self) -> None:
        """手动选择输出文件名。"""
        init = self.var_outname.get() or "output_mosaic.mp4"
        if self.src:
            init = str(self.src.parent / init)
        path = filedialog.asksaveasfilename(
            title="选择输出文件", initialfile=Path(init).name,
            initialdir=str(Path(init).parent), defaultextension=".mp4",
            filetypes=[("MP4 视频", "*.mp4")])
        if path:
            self.var_outname.set(Path(path).name)

    def on_crf_help(self) -> None:
        """解释画质参数的含义。"""
        messagebox.showinfo("画质说明", (
            "CRF（Constant Rate Factor）控制画质与体积：\n\n"
            "  18  极高画质，体积最大\n"
            "  20  高画质\n"
            "  21  推荐，举报材料清晰度与体积平衡\n"
            "  23  ffmpeg 默认值，体积较小\n"
            "  25+  体积优先，细节损失明显\n\n"
            "数值越小越清晰、体积越大。"))

    def load_file(self, path: Path, keep_regions: bool = False) -> None:
        path = Path(path)
        if not path.is_file():
            self._notify(f"文件不存在：{path}")
            return
        if not self.ffmpeg or not self.ffprobe:
            self._notify("ffmpeg 未就绪")
            return

        self._set_busy(True, "读取文件信息…")
        try:
            self.src = path
            self.cfg["last_dir"] = str(path.parent)
            config.save(self.cfg)

            info = ffmpeg_env.probe_video(path, self.ffprobe)
            if not info:
                messagebox.showerror("无法读取",
                                     "无法解析该视频文件。\n\n请确认是行车记录仪导出的 "
                                     ".h264 或视频文件。")
                return
            self.info = info

            # 裸流没有容器时长，用数帧反推
            if info.duration is None:
                info.duration = ffmpeg_env.probe_duration_by_counting(
                    self.ffprobe, path, info.fps)

            self._refresh_preview_bar()
            self._refresh_info_text()

            # VIN 预设坐标按真实分辨率等比映射
            base = tuple(self.cfg.get("vin_preset_1920x1080", VIN_PRESET_BOX))
            self.vin_box_real = scale_box(
                base, (REF_W, REF_H), (info.width, info.height))
            self._refresh_vin_text()

            # 坐标基准 = 视频真实分辨率。后面播放时会换成 640x360 的代理图，
            # 遮挡坐标仍记在这个基准上，所以必须先设好。
            self.canvas_box.set_video_size(info.width, info.height)

            # 这里不再抽帧：预览由代理文件的播放条负责。
            # 画布先清空，代理就绪后会自动显示第一帧。
            self.canvas_box.clear_image()
            self.canvas_box.set_video_size(info.width, info.height)

            if not keep_regions:
                self.canvas_box.regions = []
            self.on_regions_changed()
            self.var_outname.set(f"{path.stem}_mosaic.mp4")
            self.btn_start.config(state="normal")

            # 重置播放器状态，随后后台生成代理预览
            self._pause()
            self.proxy = None
            self.position = 0.0
            self.player_bar.set_ready(False)
            self.player_bar.seek.set_duration(self.info.duration)
            self.player_bar.update_time(0.0, self.info.duration)
            self._notify("正在生成预览，完成后即可播放…")
            self._apply_busy_state()      # 已选文件，播放条与进度条都该出现
            self._build_proxy_async(path)
        finally:
            self._set_busy(False)

    def _preview_dir(self) -> str:
        import tempfile
        d = Path(tempfile.gettempdir()) / "vin_mosaic_preview"
        d.mkdir(parents=True, exist_ok=True)
        return str(d)

    def _refresh_info_text(self) -> None:
        if not self.info or not self.src:
            self.lbl_info.config(text="—")
            return
        i = self.info
        try:
            size_mb = self.src.stat().st_size / 1024 / 1024
        except OSError:
            size_mb = 0
        dur = hhmmss(i.duration) if i.duration else "未知"
        audio = "有" if i.has_audio else "无（裸流常见）"
        self.lbl_info.config(text=(
            f"分辨率：{i.size_str}    帧率：{i.fps:g} fps\n"
            f"时长：{dur}\n"
            f"音轨：{audio}\n"
            f"体积：{size_mb:,.0f} MB"
        ))

    def _refresh_vin_text(self) -> None:
        x, y, w, h = self.vin_box_real
        self.lbl_vinbox.config(text=f"当前坐标：x={x} y={y} w={w} h={h}")

    def on_vin_toggle(self) -> None:
        self.cfg["use_vin_preset"] = bool(self.var_vin.get())
        config.save(self.cfg)

    # ---------- 打码效果 ----------
    _LABEL_TO_KEY = {v: k for k, v in EFFECT_LABELS.items()}

    def _current_effect(self) -> str:
        return self._LABEL_TO_KEY.get(self.var_effect.get(), "solid")

    def on_effect_change(self) -> None:
        """切换打码效果：纯色不需要强度滑条，滑条含义随效果改变。"""
        eff = self._current_effect()
        if eff == "solid":
            # 纯色遮挡不可调强度，隐藏滑条避免误解
            self.sld_strength.grid_remove()
            self.lbl_strength.grid_remove()
            self.lbl_strength_val.grid_remove()
        else:
            self.lbl_strength.grid()
            self.sld_strength.grid()
            self.lbl_strength_val.grid()
            if eff == "mosaic":
                # 值越小块越大、越糊
                self.lbl_strength.config(text="块大小")
                self.sld_strength.config(to=60)
                self.sld_strength.set(21)
            else:
                # 值越大越糊
                self.lbl_strength.config(text="模糊")
                self.sld_strength.config(to=40)
                self.sld_strength.set(12)
        self.cfg["effect"] = eff
        self.cfg["intensity"] = int(self.var_strength.get() or 0)
        config.save(self.cfg)
        self.on_strength_change()

    def on_strength_change(self, _val: str | None = None) -> None:
        eff = self._current_effect()
        val = int(self.var_strength.get() or 0)
        self.cfg["effect"] = eff
        self.cfg["intensity"] = val
        config.save(self.cfg)
        if eff == "mosaic":
            self.lbl_strength_val.config(text=str(val))
        elif eff == "blur":
            self.lbl_strength_val.config(text=str(val))
        else:
            self.lbl_strength_val.config(text="")

    # ---------- 拖放 ----------
    def _register_dnd(self) -> None:
        """注册文件拖放。缺 tkinterdnd2 时降级为不可用（按钮仍可正常用）。"""
        if not _HAS_DND:
            self.ffmpeg_status.config(
                text="未启用拖放（缺 tkinterdnd2），请用按钮选文件", foreground=MUTED)
            return
        for widget in (self, self.canvas_box):
            try:
                widget.drop_target_register(DND_FILES)
                widget.dnd_bind("<<Drop>>", self.on_drop)
                widget.dnd_bind("<<DragEnter>>", self._on_drag_enter)
                widget.dnd_bind("<<DragLeave>>", self._on_drag_leave)
            except (tk.TclError, AttributeError):
                continue

    def _on_drag_enter(self, _event=None) -> None:
        self.canvas_box.set_drop_highlight(True)

    def _on_drag_leave(self, _event=None) -> None:
        self.canvas_box.set_drop_highlight(False)

    def on_drop(self, event) -> None:
        """处理拖入的文件，取第一个受支持的视频。"""
        self.canvas_box.set_drop_highlight(False)
        try:
            data = event.data
            paths = self.tk.splitlist(data) if isinstance(data, str) else list(data)
        except (tk.TclError, AttributeError, TypeError):
            return
        for raw in paths:
            p = Path(str(raw).strip("{}"))
            if p.suffix.lower() in VIDEO_EXTS and p.is_file():
                self.load_file(p)
                return
        self._notify("拖入的文件不是支持的视频格式")

    def _bind_player_keys(self) -> None:
        """键盘快捷键（全局绑定到窗口）。"""
        self.bind("<space>", lambda e: self.on_toggle_play())
        self.bind("<Left>", lambda e: self.on_seek_delta(-5))
        self.bind("<Right>", lambda e: self.on_seek_delta(5))
        self.bind("<Control-Left>", lambda e: self.on_seek_delta(-30))
        self.bind("<Control-Right>", lambda e: self.on_seek_delta(30))
        self.bind("<Home>", lambda e: self.on_seek_to(0.0))
        self.bind("<End>", lambda e: self.on_seek_to(self.info.duration
                                                     if self.info else 0.0))
        self.bind("<Prior>", lambda e: self.on_seek_delta(-30))
        self.bind("<Next>", lambda e: self.on_seek_delta(30))

    def _build_proxy_async(self, source: Path) -> None:
        """后台生成代理预览文件，完成后自动接上播放器。"""
        def work() -> None:
            def prog(sec: float, _elapsed: float) -> None:
                self._post("proxy_progress", sec)
            try:
                path = ensure_proxy(self.ffmpeg, source, ProxySpec(),
                                    on_progress=prog, cancel_event=self.cancel_event)
            except Exception:  # noqa: BLE001 - 后台异常要送回主线程显示
                traceback.print_exc()
                path = None
            self._post("proxy_ready", path)
        self.worker = threading.Thread(target=work, daemon=True)
        self.worker.start()

    def _on_proxy_progress(self, sec: float) -> None:
        """代理生成进度（此时界面还能单帧预览，只是不能播放）。"""
        dur = self.info.duration if self.info else None
        if dur:
            pct = min(100.0, sec / dur * 100)
            self.progress.set_progress(pct)
            self._show_prog(f"生成预览 {pct:.0f}%")
        else:
            self.progress.set_indeterminate(True)
            self._show_prog(f"生成预览 {hhmmss(sec)}")

    def _on_proxy_ready(self, path: Path | None) -> None:
        self.progress.reset()
        if not path or not path.is_file():
            self.player_bar.set_ready(False)
            self._set_busy(False)
            self._notify("预览文件生成失败，仍可按「预览时间点」抽帧查看，但无法播放")
            return
        self.proxy = path
        self._set_busy(False)
        dur = self.info.duration if self.info else None
        self.player_bar.seek.set_duration(dur)
        self.player_bar.set_ready(True)
        self.player_bar.update_time(0.0, dur)
        self._ready_for_seek.set()
        self._notify(f"预览已就绪（{path.stat().st_size / 1048576:.1f}MB），"
                     f"可播放 / 快进 / 拖动进度条")
        # 显示第一帧，让用户立即看到画面
        if dur and dur > 0:
            self.on_seek_to(0.0)

    # ---------- 播放控制 ----------
    def on_toggle_play(self) -> None:
        if not self.proxy:
            self._notify("预览还没生成好，稍等一下")
            return
        if self.playing:
            self._pause()
        else:
            self._play()

    def on_stop_play(self) -> None:
        """停止：暂停并回到 0 秒。

        预览时需要反复从开头看，所以这里等价于「暂停 + 归零」，
        而不是只暂停（只暂停还得自己拖回开头）。
        """
        if not self.proxy:
            self._notify("预览还没生成好，稍等一下")
            return
        # 先停掉播放与管道，避免归零过程中还在推进播放头
        self._pause()
        # 跳到开头。on_seek_to 内部会走管道取首帧并刷新画面。
        self.on_seek_to(0.0)
        self._notify("已停止并回到开头")

    def _play(self) -> None:
        dur = self.info.duration if self.info else None
        # 播到结尾后再按播放，从头开始
        if dur and self.position >= dur - 0.15:
            self.position = 0.0
            self._start_reader(0.0)
        if self.reader is None and not self._start_reader(self.position):
            self.playing = False
            self.player_bar.set_playing(False)
            self._notify("无法启动预览播放")
            return
        # 已经在播放位置附近时不要重置基准，否则第一帧耗时会被算大
        self._stream_start = time.perf_counter()
        self.playing = True
        self.player_bar.set_playing(True)
        self._schedule_next_frame(0)

    def _pause(self) -> None:
        self.playing = False
        self.player_bar.set_playing(False)
        self._cancel_play_job()
        self._stop_reader()
        self._reading = False
        self._resume_after_seek = False

    def _start_reader(self, at: float) -> bool:
        """（重新）建立帧管道，并重置时间基准。

        注意必须同时重置 _stream_start：否则 seek 之后第一帧会把
        「等待代理/seek 的那段空转时间」当成帧耗时算进去，
        位置会瞬间冲到片尾（实测从 30s 直接跳到 216s）。
        """
        if not self.proxy:
            return False
        with self._seek_lock:
            self._stop_reader()
            self._seeking = True
            self._reading = False
            rd = FrameReader(self.ffmpeg, self.proxy, fps=self.play_fps)
            if not rd.start(start_sec=max(0.0, at), speed=self.speed):
                self._seeking = False
                return False
            self.reader = rd
        self._stream_pos = max(0.0, at)
        self._stream_start = time.perf_counter()
        self._seeking = False
        return True

    def _stop_reader(self) -> None:
        rd, self.reader = self.reader, None
        if rd is not None:
            rd.stop()

    def _cancel_play_job(self) -> None:
        if self._play_job is not None:
            try:
                self.after_cancel(self._play_job)
            except (tk.TclError, ValueError):
                pass
            self._play_job = None

    def _schedule_next_frame(self, delay_ms: int) -> None:
        self._cancel_play_job()
        if self.playing:
            self._play_job = self.after(max(1, delay_ms), self._on_frame)

    def _on_frame(self) -> None:
        """调度一帧：读帧放到后台线程，主线程只负责显示与节流。

        为什么不能同步读：单帧 RGB 约 690KB，管道读取 + tkinter 重绘会阻塞
        主线程上百毫秒，表现为界面卡死、按钮无响应。
        """
        self._play_job = None
        if not self.playing or self.reader is None or self._reading:
            if self.playing and self._reading:
                self._schedule_next_frame(5)
            return

        rd = self.reader
        self._reading = True

        def grab() -> None:
            try:
                frame = rd.read_frame()
            except Exception:  # noqa: BLE001
                frame = None
            self._post("play_frame", frame)

        threading.Thread(target=grab, daemon=True).start()

    def _on_frame_ready(self, frame) -> None:
        """收到后台读到的帧。"""
        self._reading = False
        if not self.playing:
            return

        if frame is None:
            self._pause()
            dur = self.info.duration if self.info else None
            if dur:
                self.position = dur
                self.player_bar.update_time(self.position, dur)
            return

        w, h, rgb = frame
        self._show_rgb(w, h, rgb)

        # 倍速语义：spd 倍速 = 每秒显示 spd 秒的媒体内容。
        # 实现方式只有一个杠杆 —— 「帧间隔」（决定画面节奏），
        # 而位置推进必须始终等于真实流逝时间 × spd，两者才自洽。
        #
        # 关键：位置推进量不能乘 spd 之外再改帧间隔，否则会和位置自相矛盾。
        # 正确做法：
        #   帧间隔 = 标称间隔 / spd   → 0.25x 时每 267ms 一帧（画面变慢）
        #   位置 += 真实流逝时间       → 位置跟着画面一起慢，比例自然正确
        spd = max(0.25, min(4.0, float(self.speed or 1.0)))

        # 位置按真实时钟 × 倍速推进：2x 时真实过 1 秒，位置应前进 2 秒。
        # 注意 frame_cost 已经含了「等待下一帧」的时间，
        # 所以基准间隔也要按倍速放大，兜底阈值才与倍速自洽。
        now = time.perf_counter()
        frame_cost = now - self._stream_start
        self._stream_start = now
        # 兜底：单帧推进量超过标称帧间隔的 5 倍说明管道卡顿，
        # 此时按标称值推进，否则位置会瞬间冲到片尾。
        nominal = (1.0 / max(1.0, self.play_fps)) * spd
        real_step = frame_cost * spd
        step = real_step if real_step <= nominal * 5 else nominal
        self._stream_pos += step

        dur = self.info.duration if self.info else None
        if dur:
            self._stream_pos = min(self._stream_pos, dur)
        self.position = self._stream_pos
        self.player_bar.update_time(self.position, dur)

        # 只有帧间隔按倍速缩放 —— 这是画面「看起来快/慢」的唯一来源
        interval_ms = int(1000.0 / max(1.0, self.play_fps) / spd)
        delay = max(0, interval_ms - int(frame_cost * 1000))
        self._schedule_next_frame(delay)

    def _show_rgb(self, w: int, h: int, rgb: bytes) -> None:
        """把 RGB 字节显示到画布。

        用 PPM(P6) 格式直接喂给 PhotoImage —— tkinter 原生支持，
        比转成 PNG 再读快得多（实测 7.6ms vs 62.3ms）。
        """
        try:
            ppm = b"P6\n%d %d\n255\n" % (w, h) + rgb
            img = tk.PhotoImage(data=ppm)
            self._ppm_cache = img          # 必须保留引用，否则被回收后画面消失
            self.canvas_box.set_frame_image(img, w, h)
        except tk.TclError:
            self._notify("该帧无法显示")

    def on_seek_delta(self, delta: float) -> None:
        cur = self.position
        dur = self.info.duration if self.info else None
        target = max(0.0, cur + delta)
        if dur:
            target = min(target, dur - 0.05)
        self.on_seek_to(target)

    def _on_seek_frame_ready(self, target: float, frame) -> None:
        """seek 的帧到达了。若用户已再次拖动，丢弃这个过期帧。"""
        self._reading = False
        if frame is not None and abs(self.position - target) <= 0.35:
            self._show_rgb(*frame)
        # 管道已独占，此时才安全地恢复播放循环
        if getattr(self, "_resume_after_seek", False):
            self._resume_after_seek = False
            if self.playing:
                self._schedule_next_frame(0)

    def on_scrub(self, pos: float) -> None:
        """拖拽进度条过程中：暂停并预览该位置的单帧。"""
        self.position = pos
        dur = self.info.duration if self.info else None
        self.player_bar.update_time(pos, dur)

    def on_seek_to(self, pos: float) -> None:
        """跳转到指定秒数并显示该帧；原本在播放则继续播。"""
        if not self.proxy:
            self._notify("预览还没生成好")
            return
        dur = self.info.duration if self.info else None
        target = max(0.0, float(pos))
        if dur:
            target = min(target, max(0.0, dur - 0.05))
        was_playing = self.playing

        # 停掉调度与管道，但保留 playing 标记以便之后恢复
        self._cancel_play_job()
        self._stop_reader()
        self._reading = False
        self.position = target

        if self._start_reader(target):
            rd = self.reader
            self._reading = True

            def grab_one() -> None:
                try:
                    frame = rd.read_frame()
                except Exception:  # noqa: BLE001
                    frame = None
                self._post("seek_frame", target, frame)

            threading.Thread(target=grab_one, daemon=True).start()
        self._stream_pos = target
        self._stream_start = time.perf_counter()
        if dur:
            self.player_bar.update_time(target, dur)

        if was_playing:
            # 必须等 seek 的那一帧读完再启动播放循环：
            # 否则两个线程并发读同一管道，会互相消费字节流，
            # 其中一方很快读到 EOF 返回 None，被误判为"播放到结尾"。
            self.playing = True
            self.player_bar.set_playing(True)
            self._resume_after_seek = True
            if not self._reading:
                self._resume_after_seek = False
                self._schedule_next_frame(0)

    def on_speed_change(self, speed: float) -> None:
        """切换倍速。

        倍速只影响「帧间隔」，由播放循环每帧重算，
        所以这里只需更新 self.speed —— 不需要重启管道或中断播放。
        暂停时改同样有效，下次播放立即按新倍速走。
        """
        self.speed = max(0.25, min(4.0, float(speed)))
        if self.playing:
            # 立刻按新倍速调度下一帧，用户马上能看出变化
            self._cancel_play_job()
            interval_ms = int(1000.0 / max(1.0, self.play_fps) / self.speed)
            self._play_job = self.after(max(1, interval_ms), self._on_frame)

    # ---------- 处理 ----------
    def on_start(self) -> None:
        if not self.src or not self.info or self.busy:
            return
        try:
            start = parse_hhmmss(self.var_start.get())
            end = parse_hhmmss(self.var_end.get())
        except ValueError as e:
            messagebox.showwarning("时间格式错误", str(e))
            return
        if start is not None and end is not None and end <= start:
            messagebox.showwarning("时间范围错误", "结束时间必须大于开始时间。")
            return
        if end is not None and self.info.duration and end > self.info.duration + 5:
            messagebox.showwarning(
                "时间超出", f"结束时间超出视频总时长 {hhmmss(self.info.duration)}。")
            return

        out_name = (self.var_outname.get() or "").strip()
        if not out_name:
            out_name = f"{self.src.stem}_mosaic.mp4"
        if not out_name.lower().endswith(".mp4"):
            out_name += ".mp4"
        out_path = self.src.parent / out_name

        plan = MaskPlan(
            width=self.info.width, height=self.info.height,
            use_vin_preset=bool(self.var_vin.get()),
            vin_box=self.vin_box_real,
            extra=[MaskRegion(**r.to_dict()) for r in self.canvas_box.regions],
            effect=self._current_effect(),
            intensity=int(self.var_strength.get() or 0),
        )

        crf = int(self.var_crf.get())
        self.cfg["crf"] = crf
        config.save(self.cfg)

        self.cancel_event.clear()
        self._apply_busy_state()
        self._set_busy(True, "开始处理…")
        self.progress.reset()
        self._notify("")
        self.btn_play.config(state="disabled")
        self.btn_open_dir.config(state="normal")

        total = self.info.duration
        if start is not None or end is not None:
            total = (end - start) if (start is not None and end is not None) else None

        self.worker = threading.Thread(
            target=self._run_job,
            args=(out_path, plan, crf, start, end, total),
            daemon=True)
        self.worker.start()

    def _run_job(self, out_path: Path, plan: MaskPlan, crf: int,
                 start, end, total) -> None:
        def on_progress(p: packager.Progress) -> None:
            self._post("job_progress", p)

        def on_log(line: str) -> None:
            # 进度行已在 on_progress 处理，这里只留错误相关输出
            if any(k in line for k in ("Error", "error", "Invalid", "No such")):
                self._post("job_log", line.strip())

        res = packager.run_job(
            self.ffmpeg, self.src, out_path, plan,
            crf=crf, preset="slow", start_sec=start, end_sec=end,
            has_audio=self.info.has_audio if self.info else False,
            total_duration=total,
            on_progress=on_progress, on_log=on_log,
            cancel_event=self.cancel_event)
        self._post("job_done", res, out_path)

    def _update_progress(self, p: packager.Progress) -> None:
        if p.pct is not None:
            self.progress.set_progress(p.pct)
            self._show_prog(f"{p.pct:.0f}%  {hhmmss(p.seconds)}")
        else:
            # 未知总时长时改用扫动动画，至少让用户知道程序还在跑
            self.progress.set_indeterminate(True)
            self._show_prog(f"已处理 {hhmmss(p.seconds)}  {p.stage}")

    def _finish_job(self, res: packager.JobResult, out_path: Path) -> None:
        self._set_busy(False)
        if res.ok:
            self.progress.set_progress(100)
            size_mb = out_path.stat().st_size / 1024 / 1024 if out_path.is_file() else 0
            self._show_prog("完成")   # 保持可见：满格进度条 + "完成"是有效状态
            self._show_done_line(f"已生成：{out_path.name}（{size_mb:,.1f} MB）")
            self.btn_play.config(state="normal")
            self.btn_open_dir.config(state="normal")
            if self.cfg.get("auto_open_dir", True):
                self._open_path(str(out_path.parent))
            messagebox.showinfo("处理完成",
                                f"输出文件：\n{out_path}\n\n大小：{size_mb:,.1f} MB")
        else:
            self.progress.reset()
            # 失败/取消后进度条已无意义，直接隐藏，避免留一条空进度误导
            self.progress.grid_remove()
            self.lbl_prog.grid_remove()
            detail = res.stderr_tail or res.message
            messagebox.showerror("处理失败", f"{res.message}\n\n{detail[-600:]}")

    def on_cancel(self) -> None:
        self.cancel_event.set()
        self._notify("正在取消…")

    def on_open_dir(self) -> None:
        if self.src:
            d = str(self.src.parent)
        elif self.var_outname.get():
            d = str(Path(self.var_outname.get()).parent)
        else:
            d = str(Path.home())
        self._open_path(d)

    def on_play(self) -> None:
        if not self.src:
            return
        out_path = self.src.parent / self.var_outname.get()
        if out_path.is_file():
            os.startfile(str(out_path))  # type: ignore[attr-defined]
        else:
            self._notify("输出文件不存在")

    def on_close(self) -> None:
        """关闭窗口：停掉播放、杀掉 ffmpeg 子进程，避免残留后台进程。"""
        try:
            self._pause()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.cancel_event.set()
        except Exception:  # noqa: BLE001
            pass
        try:
            self.progress.destroy()
        except Exception:  # noqa: BLE001
            pass
        if self._poll_job is not None:
            try:
                self.after_cancel(self._poll_job)
            except (tk.TclError, ValueError):
                pass
            self._poll_job = None
        self.destroy()

    def _open_path(self, path: str) -> None:
        try:
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", path])
            else:
                subprocess.Popen(["xdg-open", path])
        except OSError:
            pass

    def _set_busy(self, busy: bool, msg: str = "") -> None:
        """切换忙碌状态。

        注意 busy=False 时必须清空 lbl_prog —— 之前只停了按钮和进度条，
        文字却留着「正在抽取 5 秒处的画面…」，与下方真实结果并存，
        看起来像卡住没反应（这是用户截图里报的问题）。
        """
        self.busy = busy
        if busy:
            self.btn_start.config(state="disabled")
            self.btn_cancel.config(state="normal")
            self._show_prog(msg)
        else:
            self.btn_cancel.config(state="disabled")
            self.btn_start.config(state="normal" if self.src else "disabled")
            if self.progress._indeterminate:
                self.progress.set_indeterminate(False)
                self.progress.reset()
            # 忙碌提示已完成使命，清掉它，避免与结果提示并存造成误解
            self._show_prog("")


def main() -> None:
    app = MosaicApp()
    app.protocol("WM_DELETE_WINDOW", app.on_close)
    app.mainloop()


if __name__ == "__main__":
    main()
