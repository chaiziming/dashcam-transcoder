"""合并列表的界面逻辑：从 main.py 抽出的独立模块。

原来这几十行散在 MosaicApp 里，让main.py 涨到 1500+ 行。
这里收成一个 Mixin，主类只要 `class MosaicApp(MergeListMixin, Tk)` 即可。

职责边界：
  - 只管「列表怎么显示、勾选状态怎么记、合并时取哪些文件」
  - 不碰 ffmpeg 调用（那是 packager 的事）
  - 不碰槽位卡（在 canvas.py 里），这里只处理右侧列表

勾选用「✓ 」前缀表示参与合并，点一下切换。不用 Ctrl 多选，
因为用户表达的是「这几段要、那几段不要」，逐项点击最直观。
"""
from __future__ import annotations

from pathlib import Path


class MergeListMixin:
    """合并列表的显示与勾选逻辑（混入 MosaicApp）。"""

    # ---------- 内部判定 ----------

    def _file_enabled(self, idx: int) -> bool:
        """第 idx 个文件是否参与合并（默认都参与）。"""
        disabled = getattr(self, "_files_disabled", None)
        if not disabled:
            return True
        return idx not in disabled

    def _merge_targets(self) -> list[Path]:
        """参与合并的文件（已按列表顺序，跳过被取消的）。"""
        return [f for i, f in enumerate(self.preview_files)
                if self._file_enabled(i)]

    # ---------- 显示 ----------

    def _refresh_file_list(self, *, selected: int | None = None) -> None:
        """刷新合并列表。勾选用「✓ 」前缀，取消的用全角空格。"""
        if not hasattr(self, "lst_files"):
            return
        self.lst_files.delete(0, "end")
        for i, f in enumerate(self.preview_files):
            mark = "✓ " if self._file_enabled(i) else "　 "
            self.lst_files.insert("end", f"{mark}{f.name}")
        if selected is not None and 0 <= selected < len(self.preview_files):
            self.lst_files.selection_set(selected)
        if hasattr(self, "lbl_list_hint"):
            total = len(self.preview_files)
            on = sum(1 for i in range(total) if self._file_enabled(i))
            self.lbl_list_hint.config(
                text=f"点「添加视频」开空位，把视频拖进卡片（上限 {self.MAX_MERGE} 个）"
                if total == 0
                else f"共 {total}/{self.MAX_MERGE} 个，将按顺序合并 {on} 个")

    def _refresh_preview_bar(self) -> None:
        """更新顶部的源文件提示条。"""
        if not hasattr(self, "lbl_src"):
            return
        n = len(self.preview_files)
        if n == 0:
            self.lbl_src.config(text="未选择文件")
            return
        names = "、".join(f.name for f in self.preview_files[:3])
        more = f" 等 {n} 个" if n > 3 else ""
        cur = self.src.name if getattr(self, "src", None) else ""
        self.lbl_src.config(
            text=f"待合并 {n} 个：{names}{more}"
                 + (f"   （当前：{cur}）" if cur else ""))

    # ---------- 交互 ----------

    def _on_file_item_toggle(self, _event=None) -> None:
        """点击列表项= 勾选/取消（取消的项不参与合并）。"""
        sel = self.lst_files.curselection()
        if not sel:
            return
        idx = sel[0]
        disabled = getattr(self, "_files_disabled", None)
        if disabled is None:
            disabled = set()
        if idx in disabled:
            disabled.discard(idx)
        else:
            disabled.add(idx)
        self._files_disabled = disabled
        self._refresh_file_list(selected=idx)

    def on_remove_file(self) -> None:
        """从合并列表移除选中的文件。"""
        sel = self.lst_files.curselection()
        if not sel:
            self._notify("请先在列表里选中要移除的文件")
            return
        idx = sel[0]
        if not (0 <= idx < len(self.preview_files)):
            return
        removed = self.preview_files.pop(idx)
        disabled = getattr(self, "_files_disabled", set())
        # 索引整体前移一位，取消标记也要跟着挪
        disabled.discard(idx)
        self._files_disabled = {i - 1 if i > idx else i for i in disabled}
        # 移除的正好是当前预览的文件 -> 换成剩余的第一个
        if getattr(self, "src", None) == removed:
            self.src = None
            if self.preview_files:
                self.load_file(self.preview_files[0])
        self._refresh_file_list()
        self._refresh_preview_bar()

    def on_clear_files(self) -> None:
        """清空合并列表（同时清掉画面上的槽位卡）。"""
        self.preview_files.clear()
        self._files_disabled = set()
        if hasattr(self, "canvas_box"):
            self.canvas_box.clear_slots()
        self._refresh_file_list()
        self._refresh_preview_bar()