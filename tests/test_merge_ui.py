"""UI 层验证「合并视频」按钮与分段列表：存在、可点、点击后正确弹确认框。

模型说明（v6 起）：
  左区是 N 个**独立画布**（每段一个），右侧列表是这些段的「镜像视图」。
  所以这里不再有 preview_files / canvas_box.slots，
  统一用 app.segments（段模型）来断言。
"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as gm  # noqa: E402
from app.segment import Segment  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


def make_segment(app, index: int, file: Path) -> Segment:
    """塞一个「已载入」的段（不真的解码，只填 file 字段）。"""
    seg = app.segments[index]
    seg.file = file
    return seg


def main() -> int:
    app = gm.MosaicApp()
    app.withdraw()

    print("\n== 1. 按钮存在与可用状态 ==")
    check("合并按钮已创建", hasattr(app, "btn_merge"))
    check("按钮文案为「合并」",
          "合并" in str(app.btn_merge.cget("text")),
          str(app.btn_merge.cget("text")))

    # ffmpeg 未就绪时应禁用
    app.ffmpeg = None
    app._sync_merge_btn()
    check("ffmpeg 未就绪时禁用", str(app.btn_merge["state"]) == "disabled")

    app.ffmpeg = r"C:\fake\ffmpeg.exe"
    app._sync_merge_btn()
    check("ffmpeg 就绪后启用", str(app.btn_merge["state"]) == "normal")
    app.ffmpeg = None

    print("\n== 2. 一个视频都没有时应提示去拖放 ==")
    app.ffmpeg = r"C:\fake\ffmpeg.exe"
    app.ffprobe = r"C:\fake\ffprobe.exe"
    notified: list[str] = []
    # 关键断言：合并时不应再弹文件选择框（直接用已载入的段）
    with mock.patch.object(gm.filedialog, "askopenfilenames") as picker, \
         mock.patch.object(app, "_notify", side_effect=notified.append):
        app.on_merge()
        check("合并时不再弹文件选择框", not picker.called,
              "应直接使用已载入的画布")
        check("提示去添加视频（拖放）",
              any("拖" in n for n in notified),
              f"提示: {notified}")

    print("\n== 3. 只有 1 个视频时也应中止 ==")
    tmp = Path(__file__).resolve().parent.parent / "build_assets"
    tmp.mkdir(exist_ok=True)
    f1 = tmp / "seg1.h264"
    f1.write_bytes(b"\x00" * 1024)
    make_segment(app, 0, f1)
    app._sync_segments_to_list()
    notified.clear()
    with mock.patch.object(gm.messagebox, "askyesno") as yn, \
         mock.patch.object(app, "_notify", side_effect=notified.append):
        app.on_merge()
        check("1 个视频时不弹确认框", not yn.called)
        check("提示数量不足", any("无法合并" in n for n in notified),
              f"提示: {notified}")

    print("\n== 4. 已载入 2 个视频 -> 走合并流程 ==")
    f2 = tmp / "seg2.h264"
    f2.write_bytes(b"\x00" * 1024)
    # 第 2 段还不存在，先长出来
    app.on_add_video()
    make_segment(app, 1, f2)
    app._sync_segments_to_list()

    check("段顺序 = 添加顺序",
          [s.file.name for s in app.segments if s.file] == ["seg1.h264", "seg2.h264"],
          str([s.file.name for s in app.segments if s.file]))

    # 用户在确认框点「否」-> 应中止，不弹保存框
    with mock.patch.object(gm.messagebox, "askyesno", return_value=False), \
         mock.patch.object(gm.filedialog, "asksaveasfilename") as save:
        app.on_merge()
        check("确认框点否时不保存", not save.called)

    # 用户一路确认 -> 应构造合并命令并执行
    ran: dict = {}

    def fake_run_merge(cmd, output_path, **kw):
        ran["cmd"] = cmd
        ran["output"] = output_path
        return gm.packager.JobResult(True, str(output_path), 0, "完成")

    built: dict = {}
    real_build = gm.packager.build_merge_command

    def fake_build(*a, **kw):
        built["args"] = a
        built["kwargs"] = kw
        return real_build(*a, **kw)

    with mock.patch.object(gm.messagebox, "askyesno", return_value=True), \
         mock.patch.object(gm.filedialog, "asksaveasfilename",
                           return_value=str(tmp / "merged.mp4")), \
         mock.patch.object(gm.messagebox, "showinfo"), \
         mock.patch.object(gm.packager, "build_merge_command", fake_build), \
         mock.patch.object(gm.packager, "run_merge_job", fake_run_merge), \
         mock.patch.object(app, "_estimate_merge_seconds",
                           return_value=10.0):
        app.ffmpeg = r"C:\fake\ffmpeg.exe"
        app.on_merge()
        import time
        for _ in range(50):
            if ran:
                break
            time.sleep(0.1)

    check("调用了 build_merge_command", bool(built))
    check("调用了 run_merge_job 执行", bool(ran))
    if built:
        a = built["args"]
        check("传入两个已载入的文件",
              len(a[1]) == 2, f"{[Path(x).name for x in a[1]]}")
        check("传入遮挡方案", a[2] is not None, type(a[2]).__name__)
        check("传入 crf 等参数",
              "crf" in built["kwargs"], str(list(built["kwargs"].keys())))
    if ran:
        n_in = ran["cmd"].count("-i")
        check("命令有两个输入", n_in == 2, f"-i 出现 {n_in} 次")
        check("命令含 concat", "concat" in " ".join(ran["cmd"]))

    print("\n== 5. 段数上限 ==")
    notified1: list[str] = []
    with mock.patch.object(app, "load_file"), \
            mock.patch.object(app, "_notify", side_effect=notified1.append):
        for _ in range(6):
            app.on_add_video()
    check(f"段数最多 {app.MAX_MERGE} 个",
          len(app.segments) == app.MAX_MERGE,
          f"{len(app.segments)} 个")
    check("超限时提示",
          any("最多" in n for n in notified1), f"提示: {notified1}")

    print("\n== 5b. 移除一段后段数下降 ==")
    app.remove_segment(1)
    check("移除后段数 -1", len(app.segments) == app.MAX_MERGE - 1,
          f"{len(app.segments)}")
    check("剩余段重新编号",
          [s.index for s in app.segments] == list(range(len(app.segments))),
          str([s.index for s in app.segments]))
    # 补回第 2 段并重新载入文件
    app.on_add_video()
    make_segment(app, 1, f2)
    app._sync_segments_to_list()

    print("\n== 6. 合并顺序 ==")
    names = [s.file.name for s in app.segments if s.file]
    check("合并顺序 = 画布顺序", names == ["seg1.h264", "seg2.h264"], f"{names}")
    targets = [f.name for f in app._merge_targets()]
    check("合并目标顺序与画布一致", targets == names, f"{targets}")

    print("\n== 7. 勾选/取消：被取消的项不参与合并 ==")
    before = len(app._merge_targets())
    check("全部勾选时合并目标数 = 文件数", before == len(names),
          f"{before} / {len(names)}")
    app.lst_files.selection_set(1)
    app._on_file_item_toggle()
    check("取消第 2 项后合并目标少 1 个",
          len(app._merge_targets()) == before - 1,
          f"{len(app._merge_targets())} / {before}")
    check("列表项前缀变为未勾选",
          app.lst_files.get(1).startswith("\u3000"),
          repr(app.lst_files.get(1)[:4]))
    app.lst_files.selection_set(1)
    app._on_file_item_toggle()
    check("再次点击可恢复勾选", len(app._merge_targets()) == before,
          f"{len(app._merge_targets())}")

    print("\n== 8. 移除与清空 ==")
    n0 = len([s for s in app.segments if s.file])
    seg_total = len(app.segments)
    app.lst_files.selection_set(0)
    app.on_remove_file()
    check("移除选中项（清空该段视频，画布保留）",
          len([s for s in app.segments if s.file]) == n0 - 1,
          f"{n0} -> {len([s for s in app.segments if s.file])}")
    check("画布数量不变（段还在）",
          len(app.segments) == seg_total,
          f"{len(app.segments)} 段")
    app.on_clear_files()
    check("清空所有段的视频", len([s for s in app.segments if s.file]) == 0)
    check("清空后合并目标为空", len(app._merge_targets()) == 0)
    check("清空后画布仍保留", len(app.segments) >= 1, f"{len(app.segments)}")

    for f in [f1, f2]:
        f.unlink(missing_ok=True)
    try:
        tmp.rmdir()
    except OSError:
        pass

    try:
        app.destroy()
    except tk.TclError:
        pass

    passed = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    print("\n" + "=" * 54)
    print(f"结果：{passed}/{total} 通过")
    if passed != total:
        print("\n失败项：")
        for name, ok, detail in results:
            if not ok:
                print(f"  - {name}  {detail}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
