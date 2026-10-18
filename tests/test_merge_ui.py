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
    check("按钮文案为「合并视频…」",
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

    print("\n== 2. 点击后进入选择流程（模拟用户点按钮）==")
    # on_merge 开头会检查 ffmpeg 是否就绪，这里先置为有效值
    app.ffmpeg = r"C:\fake\ffmpeg.exe"
    app.ffprobe = r"C:\fake\ffprobe.exe"

    # 模拟：用户选了 1 个文件 -> 应提示至少选 2 个并中止
    notified: list[str] = []
    with mock.patch.object(gm.filedialog, "askopenfilenames",
                           return_value=("a.h264",)) as m1, \
         mock.patch.object(app, "_notify", side_effect=notified.append):
        app.on_merge()
        check("已弹出多选对话框", m1.called)
        check("仅选 1 个时给出提示", any("至少" in n for n in notified),
              f"提示内容: {notified}")

    # 模拟：用户直接取消
    with mock.patch.object(gm.filedialog, "askopenfilenames",
                           return_value=()):
        app.on_merge()
        check("取消选择时不报错", True, "已正常返回")

    print("\n== 3. 选中多个文件后的流程 ==")
    tmp = Path(__file__).resolve().parent.parent / "build_assets"
    tmp.mkdir(exist_ok=True)
    f1 = tmp / "seg1.h264"
    f2 = tmp / "seg2.h264"
    f1.write_bytes(b"\x00" * 1024)
    f2.write_bytes(b"\x00" * 1024)

    # 用户在确认框点「否」-> 应中止，不弹保存框
    with mock.patch.object(gm.filedialog, "askopenfilenames",
                           return_value=(str(f1), str(f2))), \
         mock.patch.object(gm.messagebox, "askyesno", return_value=False), \
         mock.patch.object(gm.filedialog, "asksaveasfilename") as save:
        app.on_merge()
        check("确认框点否时不保存", not save.called)

    # 用户一路确认 -> 应构造合并命令并执行
    # 注意：只 mock 真正要断言的两处，别整个 mock 掉 packager 模块，
    # 否则内部调用（如 _estimate_merge_seconds_for）也会被替换而掩盖真问题。
    ran: dict = {}
    real_run_merge = gm.packager.run_merge_job

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

    with mock.patch.object(gm.filedialog, "askopenfilenames",
                           return_value=(str(f1), str(f2))), \
         mock.patch.object(gm.messagebox, "askyesno", return_value=True), \
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
        # 后台线程是 daemon，等它跑完
        import time
        for _ in range(50):
            if ran:
                break
            time.sleep(0.1)

    check("调用了 build_merge_command", bool(built))
    check("调用了 run_merge_job 执行", bool(ran))
    if built:
        a = built["args"]
        check("传入了遮挡方案", a[2] is not None, type(a[2]).__name__)
        check("传入了 crf 等参数",
              "crf" in built["kwargs"], str(list(built["kwargs"].keys())))
        check("命令含两个 -i 输入",
              built and a and True, "")
    if ran:
        n_in = ran["cmd"].count("-i")
        check("命令有两个输入", n_in == 2, f"-i 出现 {n_in} 次")
        check("命令含 concat", "concat" in " ".join(ran["cmd"]))
        check("裸流补了 -f h264", "h264" in ran["cmd"])

    for p in (f1, f2):
        p.unlink(missing_ok=True)
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