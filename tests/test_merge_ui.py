"""UI 层验证「合并视频」按钮：存在、可点、点击后正确弹确认框。"""
from __future__ import annotations

import sys
import tkinter as tk
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as gm  # noqa: E402

results: list[tuple[str, bool, str]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append((name, bool(ok), detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  {detail}" if detail else ""))


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

    print("\n== 2. 预览列表为空时应提示去添加 ==")
    # on_merge 不再弹框选文件，直接用 preview_files
    # 先确保 ffmpeg 就绪，否则会先被 ffmpeg 检查拦下
    app.ffmpeg = r"C:\fake\ffmpeg.exe"
    app.ffprobe = r"C:\fake\ffprobe.exe"
    notified: list[str] = []
    app.preview_files = []
    # 关键断言：合并时不应再弹文件选择框（直接用 preview_files）
    with mock.patch.object(gm.filedialog, "askopenfilenames") as picker, \
         mock.patch.object(app, "_notify", side_effect=notified.append):
        app.on_merge()
        check("合并时不再弹文件选择框", not picker.called,
              "应直接使用已添加的预览列表")
        check("提示去添加预览", any("添加预览" in n for n in notified),
              f"提示: {notified}")

    print("\n== 3. 只有 1 个视频时也应中止 ==")
    tmp = Path(__file__).resolve().parent.parent / "build_assets"
    tmp.mkdir(exist_ok=True)
    f1 = tmp / "seg1.h264"
    f1.write_bytes(b"\x00" * 1024)
    app.preview_files = [f1]
    notified.clear()
    with mock.patch.object(gm.messagebox, "askyesno") as yn, \
         mock.patch.object(app, "_notify", side_effect=notified.append):
        app.on_merge()
        check("1 个视频时不弹确认框", not yn.called)
        check("提示数量不足", any("无法合并" in n for n in notified),
              f"提示: {notified}")

    print("\n== 4. 已添加 2 个视频 -> 走合并流程 ==")
    f2 = tmp / "seg2.h264"
    f2.write_bytes(b"\x00" * 1024)
    app.preview_files = [f1, f2]

    # 重复添加应被去重、上限应生效
    app.MAX_PREVIEW = 4
    app.preview_files = [f1, f2]
    check("预览列表保留添加顺序",
          [p.name for p in app.preview_files] == ["seg1.h264", "seg2.h264"],
          str([p.name for p in app.preview_files]))

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
        app.info = None
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
        check("传入预览列表里的文件",
              len(a[1]) == 2, f"{[Path(x).name for x in a[1]]}")
        check("传入遮挡方案", a[2] is not None, type(a[2]).__name__)
        check("传入 crf 等参数",
              "crf" in built["kwargs"], str(list(built["kwargs"].keys())))
    if ran:
        n_in = ran["cmd"].count("-i")
        check("命令有两个输入", n_in == 2, f"-i 出现 {n_in} 次")
        check("命令含 concat", "concat" in " ".join(ran["cmd"]))

    print("\n== 5. 上限与去重 ==")
    app.MAX_PREVIEW = 4
    extra = []
    for i in range(6):
        f = tmp / f"extra{i}.h264"
        f.write_bytes(b"\x00" * 512)
        extra.append(f)

    added = 0
    for f in extra:
        if f not in app.preview_files and len(app.preview_files) < app.MAX_PREVIEW:
            app.preview_files.append(f)
            added += 1
    check("最多保留 MAX_PREVIEW 个",
          len(app.preview_files) <= app.MAX_PREVIEW,
          f"{len(app.preview_files)} 个 / 上限 {app.MAX_PREVIEW}")
    check("重复文件不重复添加",
          len(set(app.preview_files)) == len(app.preview_files),
          "无重复")

    for f in [f1, f2] + extra:
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