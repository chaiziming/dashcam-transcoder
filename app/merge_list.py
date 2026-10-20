"""合并列表的界面逻辑：从 main.py 抽出的独立模块。

原来这几十行散在 MosaicApp 里，让main.py 涨到 1500+ 行。
这里收成一个 Mixin，主类只要 `class MosaicApp(MergeListMixin, Tk)` 即可。

职责边界：
  - 只管「列表怎么显示、勾选状态怎么记、合并时取哪些文件」
  - 不碰 ffmpeg 调用（那是 packager 的事）
  - 不碰画布（在 canvas.py 里），这里只处理右侧列表

勾选用「✓ 」前缀表示参与合并，点一下切换。不用 Ctrl 多选，
因为用户表达的是「这几段要、那几段不要」，逐项点击最直观。

新版模型：左区是多个独立画布（每段一个），右侧列表只是这些段的
「镜像视图」—— 列出已载入的段，可勾选/取消，可移除。
"""
from __future__ import annotations

from pathlib import Path


class MergeListMixin:
    """合并列表的显示与勾选逻辑（混入 MosaicApp）。"""

    # ---------- 内部判定 ----------

    def _file_enabled(self, seg_index: int) -> bool:
        """第 seg_index 段是否参与合并（默认都参与）。"""
        disabled = getattr(self, "_files_disabled", None)
        if not disabled:
            return True
        return seg_index not in disabled

    def _merge_targets(self) -> list[Path]:
        """参与合并的文件（已按段顺序，跳过被取消的）。"""
        out: list[Path] = []
        for i, seg in enumerate(getattr(self, "segments", [])):
            if seg.file is not None and self._file_enabled(i):
                out.append(seg.file)
        return out

    def _filled_segments(self) -> list[int]:
        """已载入文件的段索引（按顺序）。"""
        return [i for i, s in enumerate(getattr(self, "segments", []))
                if s.file is not None]

    # ---------- 显示 ----------

    def _refresh_file_list(self, *, selected: int | None = None) -> None:
        """刷新合并列表。勾选用「✓ 」前缀，取消的用全角空格。

        列表只显示**已载入文件**的段；列表第 N 行对应哪个段
        由 _list_seg_map 维护（因为可能有空画布夹在中间）。
        """
        if not hasattr(self, "lst_files"):
            return
        self.lst_files.delete(0, "end")
        self._list_seg_map: list[int] = []
        for i in self._filled_segments():
            seg = self.segments[i]
            mark = "✓ " if self._file_enabled(i) else "　 "
            self._list_seg_map.append(i)
            self.lst_files.insert("end", f"{mark}{seg.file.name}")
        # 注意：Listbox 没有 "curselection" 这个 cget 选项，
        # 选中项要用 selection_set / selection_range 读写，
        # 直接 cget 会抛 TclError（unknown option "-curselection"）。
        if selected is not None and 0 <= selected < self.lst_files.size():
            self.lst_files.selection_clear(0, "end")
            self.lst_files.selection_set(selected)
        if hasattr(self, "lbl_list_hint"):
            total = len(self._list_seg_map)
            on = sum(1 for i in self._list_seg_map if self._file_enabled(i))
            if total == 0:
                self.lbl_list_hint.config(
                    text="点「添加视频」再开一个画布，把视频拖进画布")
            else:
                self.lbl_list_hint.config(
                    text=f"共 {total}/{self.MAX_MERGE} 个，将按顺序合并 {on} 个")

    def _refresh_preview_bar(self) -> None:
        """刷新顶栏状态位。

        顶栏的「未选择文件」标签已移除（画布已能表达状态），
        这里改为写入状态栏，让用户知道当前待合并/当前预览的是哪个。
        """
        n = len(self._filled_segments())
        if n == 0:
            # 空态不占视觉：顶栏留白（画布本身已经说明了「这里是空的」）
            text = ""
        else:
            cur = self.segments[self.active_index].file.name \
                if 0 <= self.active_index < len(self.segments) \
                and self.segments[self.active_index].file else ""
            on = len(self._merge_targets())
            text = f"待合并 {n} 个" + (f"，当前预览：{cur}" if cur else "")
            if on != n:
                text += f"（{n - on} 个已取消勾选）"
        if hasattr(self, "lbl_status"):
            self.lbl_status.config(text=text)

    # ---------- 交互 ----------

    def _on_file_item_toggle(self, _event=None) -> None:
        """点击列表项 = 勾选/取消（取消的项不参与合并）。"""
        sel = self.lst_files.curselection()
        if not sel:
            return
        row = sel[0]
        seg_map = getattr(self, "_list_seg_map", [])
        if row >= len(seg_map):
            return
        idx = seg_map[row]
        disabled = getattr(self, "_files_disabled", None)
        if disabled is None:
            disabled = set()
        if idx in disabled:
            disabled.discard(idx)
        else:
            disabled.add(idx)
        self._files_disabled = disabled
        self._refresh_file_list(selected=row)
        self._refresh_preview_bar()

    def on_remove_file(self) -> None:
        """从合并列表移除选中的文件（清空那一段的画布，段本身保留）。"""
        sel = self.lst_files.curselection()
        if not sel:
            self._notify("请先在列表里选中要移除的文件")
            return
        row = sel[0]
        seg_map = getattr(self, "_list_seg_map", [])
        if row >= len(seg_map):
            return
        idx = seg_map[row]
        self.clear_segment(idx)
        self._refresh_file_list()
        self._refresh_preview_bar()

    def on_clear_files(self) -> None:
        """清空所有已载入的视频（画布保留，变成空画布）。"""
        for i, seg in enumerate(getattr(self, "segments", [])):
            if seg.file is not None:
                self.clear_segment(i)
        self._files_disabled = set()
        self._refresh_file_list()
        self._refresh_preview_bar()
