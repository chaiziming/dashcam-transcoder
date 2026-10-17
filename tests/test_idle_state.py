"""空状态测试：没载入任何视频时，不该出现"占位式"的控件与文案。

背景：用户截图指出刚打开软件时，播放条上写着"正在生成预览，播放功能即将可用"、
左下角写着"就绪"，但此时根本没有任何任务，这些文案只会让人困惑。
本测试把"空闲时该隐藏什么"固化成断言，防止回归。

判断元素是否可见用 winfo_manager()：被 grid_remove() 后返回空字符串。
注意必须先 app.update_idletasks() + app.update()，否则几何不生效。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app import main as gm  # noqa: E402
from tests.sample_helper import require_sample  # noqa: E402
from tests.sample_helper import require_sample  # noqa: E402

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
_r: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _r.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  -> {detail}" if detail else ""))


def visible(w) -> bool:
    return w.winfo_manager() != ""


def main() -> int:
    print("=" * 58)
    print("空状态（未载入视频）界面检查")
    print("=" * 58)

    app = gm.MosaicApp()
    app.update_idletasks()
    app.update()

    def vis(w) -> bool:
        return w.winfo_manager() != ""

    print("\n== 1. 空闲时应隐藏的元素 ==")
    check("分段进度条隐藏", not vis(app.progress), app.progress.winfo_manager())
    check("状态文字标签隐藏", not vis(app.lbl_prog), app.lbl_prog.winfo_manager())
    check("结果提示行隐藏", not vis(app.lbl_done), app.lbl_done.winfo_manager())
    check("播放控制条隐藏", not vis(app.player_bar), app.player_bar.winfo_manager())
    check("框选操作指引隐藏", not vis(app.lbl_hint), app.lbl_hint.winfo_manager())

    print("\n== 2. 不该有任何占位文案 ==")
    all_text = " ".join([
        app.lbl_prog.cget("text"), app.lbl_done.cget("text"),
        app.lbl_src.cget("text"), app.lbl_hint.cget("text"),
        app.player_bar.lbl_time.cget("text"),
    ])
    check("无「就绪」字样", "就绪" not in all_text, all_text.strip()[:70])
    check("无「正在生成预览」字样", "正在生成" not in all_text)
    check("状态文字为空", app.lbl_prog.cget("text") == "",
          repr(app.lbl_prog.cget("text")))
    check("结果文字为空", app.lbl_done.cget("text") == "",
          repr(app.lbl_done.cget("text")))
    check("时间显示为占位 --:--", "--:--" in app.player_bar.lbl_time.cget("text"),
          app.player_bar.lbl_time.cget("text"))

    print("\n== 3. 按钮状态应为空闲态 ==")
    check("「开始处理」禁用", str(app.btn_start.cget("state")) == "disabled",
          app.btn_start.cget("state"))
    check("「取消」禁用", str(app.btn_cancel.cget("state")) == "disabled",
          app.btn_cancel.cget("state"))
    check("播放按钮禁用", str(app.player_bar.btn_play.cget("state")) == "disabled",
          app.player_bar.btn_play.cget("state"))
    check("快进按钮禁用",
          str(app.player_bar.btn_fwd5.cget("state")) == "disabled")

    print("\n== 4. 空画布给出可操作指引（这是该显示的）==")
    check("画布无图", app.canvas_box.image is None)
    check("文件名显示「未选择文件」",
          app.lbl_src.cget("text") == "未选择文件", app.lbl_src.cget("text"))

    print("\n== 5. 载入文件后应恢复显示 ==")
    src = require_sample()
    if src is None:
        print("  [SKIP] 未找到 h264 样本，跳过需要视频的用例")
        app.on_close()
        return 0
    app.load_file(src)
    app.update()
    check("进度条出现", vis(app.progress), app.progress.winfo_manager())
    # 状态文字「有内容才显示」。载入瞬间可能正好没文案（不再抽帧），
    # 故只校验内容与显隐一致，不强制它可见。
    check("状态文字内容与显隐一致",
          (app.lbl_prog.cget("text") == "") != vis(app.lbl_prog),
          f"text={app.lbl_prog.cget('text')!r} visible={vis(app.lbl_prog)}")
    check("播放控制条出现", vis(app.player_bar), app.player_bar.winfo_manager())
    check("框选指引出现", vis(app.lbl_hint))
    check("「开始处理」可用", str(app.btn_start.cget("state")) == "normal")

    print("\n== 6. 提示行的显示/隐藏随内容 ==")
    app._notify("")
    app.update()
    check("清空后提示行隐藏", not vis(app.lbl_done), app.lbl_done.winfo_manager())
    app._notify("这是一条测试消息")
    app.update()
    check("有内容时提示行显示", vis(app.lbl_done))
    check("提示文案正确", app.lbl_done.cget("text") == "这是一条测试消息",
          app.lbl_done.cget("text"))
    app._notify("")
    app.update()

    print("\n== 7. 代理就绪后进度条仍可见（结果态不算空闲）==")
    t0 = time.time()
    while not app.proxy and time.time() - t0 < 90:
        app.update()
        time.sleep(0.03)
    check("代理已就绪", app.proxy is not None)
    check("就绪后进度条仍可见", vis(app.progress), app.progress.winfo_manager())

    app.on_close()
    ok = sum(_r)
    print(f"\n{'=' * 58}\n结果：{ok}/{len(_r)} 通过")
    return 0 if ok == len(_r) else 1


if __name__ == "__main__":
    raise SystemExit(main())