"""GUI 冒烟测试：无头模式下实例化界面，抓出所有组件构造错误。

背景：ttk 组件不支持 tkinter 专有选项（-bg/-highlightbackground/-bd 等），
这类错误只在真正创建控件时才抛 TclError，靠肉眼 review 很难一次找全。
这里直接建一个隐藏窗口把整个 UI 构造一遍，逐个验证。
"""
from __future__ import annotations

import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# ttk 组件不支持的 tkinter 专有选项
# 注意：state/width/height/wraplength/justify 经实测在本机 ttk 可用，
# 不要列入黑名单（会误报）。这里只放真正会让 ttk 抛 TclError 的
# tkinter 专有外观选项。
TTK_FORBIDDEN = {
    "bg", "background", "bd", "highlightbackground", "highlightthickness",
    "fg", "foreground", "relief", "borderwidth", "cursor", "takefocus",
    "insertwidth", "insertbackground", "selectbackground", "selectforeground",
    "exportselection", "activestyle",
}

PASS, FAIL = "\033[92mPASS\033[0m", "\033[91mFAIL\033[0m"
_r: list[bool] = []


def check(name: str, cond: bool, detail: str = "") -> None:
    _r.append(bool(cond))
    print(f"  [{PASS if cond else FAIL}] {name}" + (f"  -> {detail}" if detail else ""))


def scan_source() -> None:
    """静态扫描：找出 ttk.Xxx(... , bg=...) 这类误用。"""
    print("\n== 1. 静态扫描 ttk 组件的非法选项 ==")
    import re
    src = Path(__file__).resolve().parent.parent / "app" / "main.py"
    text = src.read_text(encoding="utf-8")
    problems: list[str] = []
    for m in re.finditer(r"ttk\.(\w+)\(", text):
        widget = m.group(1)
        if widget == "Style":
            continue
        # 从匹配处截取该调用的参数部分（到括号平衡）
        i = m.end()
        depth = 1
        buf = []
        while i < len(text) and depth:
            if text[i] == "(":
                depth += 1
            elif text[i] == ")":
                depth -= 1
                if depth == 0:
                    break
            buf.append(text[i])
            i += 1
        args = "".join(buf)
        # 找 kwargs
        for kw in re.findall(r"(?<![\w\"'])(\w+)\s*=", args):
            if kw in TTK_FORBIDDEN:
                problems.append(f"ttk.{widget}(... {kw}= ...)")
    check("无 ttk 非法选项", not problems, "; ".join(sorted(set(problems))) or "干净")
    if problems:
        print("     发现问题：")
        for p_ in sorted(set(problems)):
            print(f"       - {p_}")


def build_ui() -> None:
    print("\n== 2. 实际构建界面（捕获真实运行错误） ==")
    try:
        import tkinter as tk
        from app import main as gm
    except Exception as e:  # noqa: BLE001
        check("导入 GUI 模块", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return

    app = None
    try:
        app = gm.MosaicApp()
        # 注意：这里不能用 withdraw()。withdraw 后 winfo_width/height 全是 1，
        # 无法验证布局是否重叠（本次的按钮被标签遮挡就是这样被发现的）。
        app.update_idletasks()
        app.update()
        check("MosaicApp 实例化", True)
    except Exception as e:  # noqa: BLE001
        check("MosaicApp 实例化", False, f"{type(e).__name__}: {e}")
        traceback.print_exc()
        return

    # 逐项检查关键控件存在
    for name in ("btn_choose", "btn_start", "btn_cancel", "btn_play",
                 "btn_open_dir", "canvas_box", "lst_regions",
                 "tin_start", "tin_end", "cmb_crf", "var_vin", "var_crf"):
        check(f"控件 {name} 已创建", hasattr(app, name))

    # 画布空态应能绘制
    try:
        app.canvas_box._redraw()
        check("画空空态可绘制", True)
    except Exception as e:  # noqa: BLE001
        check("画空空态可绘制", False, f"{type(e).__name__}: {e}")

    # 时间解析逻辑
    from app.main import parse_hhmmss, hhmmss
    cases = [("", None), ("00:01:00", 60.0), ("01:00", 60.0), ("90", 90.0),
             ("00:00:00", 0.0)]
    ok = True
    detail = []
    for text_in, want in cases:
        try:
            got = parse_hhmmss(text_in)
            if want is not None and abs(got - want) > 0.01:
                ok = False
                detail.append(f"{text_in!r}->{got} 期望 {want}")
        except Exception as e:  # noqa: BLE001
            ok = False
            detail.append(f"{text_in!r} 抛错 {e}")
    check("时间解析正确", ok, "; ".join(detail) or "全部符合预期")

    # 非法输入应抛 ValueError（而不是静默通过）
    try:
        parse_hhmmss("abc")
        check("非法时间被拒绝", False, "未抛异常")
    except ValueError:
        check("非法时间被拒绝", True)

    check("时长格式化正确", hhmmss(3661) == "01:01:01", hhmmss(3661))

    # VIN 开关切换应刷新区域列表
    try:
        app.var_vin.set(False)
        app.on_vin_toggle()
        n_off = app.lst_regions.size()
        app.var_vin.set(True)
        app.on_vin_toggle()
        n_on = app.lst_regions.size()
        check("VIN 开关生效", n_on >= n_off, f"关={n_off} 开={n_on}")
    except Exception as e:  # noqa: BLE001
        check("VIN 开关生效", False, f"{type(e).__name__}: {e}")

    # 清空自定义框（空列表不应报错）
    try:
        app.on_clear_regions()
        check("清空遮挡框（空态）", True)
    except Exception as e:  # noqa: BLE001
        check("清空遮挡框（空态）", False, f"{type(e).__name__}: {e}")

    # ---- 布局不重叠（回归项：曾出现按钮与文件名标签 grid 在同一行互相遮挡）----
    print("\n== 3. 布局检查（控件不得重叠）==")
    def bx(w):
        return w.winfo_x(), w.winfo_y(), w.winfo_width(), w.winfo_height()

    def overlaps(a, b) -> bool:
        ax, ay, aw, ah = bx(a); bx2, by2, bw2, bh2 = bx(b)
        return not (ax + aw <= bx2 or bx2 + bw2 <= ax or ay + ah <= by2 or by2 + bh2 <= ay)

    try:
        b = bx(app.btn_choose); lb = bx(app.lbl_status)
        check("控件已真实布局(非1x1)", b[2] > 10 and b[3] > 10, f"btn={b[2]}x{b[3]}")
        check("添加视频按钮 与 状态标签 不重叠", not overlaps(app.btn_choose, app.lbl_status),
              f"btn={b} lbl={lb}")
        check("状态标签 位于按钮右侧", lb[0] >= b[0] + b[2],
              f"lbl.x={lb[0]} 按钮右缘={b[0]+b[2]}")
        # 画布现在嵌在分段卡片里，坐标是相对卡片的，
        # 必须换算到窗口绝对坐标才能和顶栏按钮比较。
        cv_w = app.canvas_box
        cv = (cv_w.winfo_rootx(), cv_w.winfo_rooty(),
              cv_w.winfo_width(), cv_w.winfo_height())
        btn_root_y = app.btn_choose.winfo_rooty()
        btn_root_h = app.btn_choose.winfo_height()
        check("画布位于操作条下方", cv[1] >= btn_root_y + btn_root_h,
              f"canvas.rooty={cv[1]} 操作条底={btn_root_y + btn_root_h}")
        # 「换帧预览」按钮与「预览时间点」输入框已在 v5 移除（进度条拖拽已覆盖同样需求）
        check("「换帧预览」按钮已移除", not hasattr(app, "btn_preview"))
        check("「预览时间点」输入框已移除", not hasattr(app, "ent_ts"))
    except Exception as e:  # noqa: BLE001
        check("布局检查", False, f"{type(e).__name__}: {e}")

    # ---- 打码效果 ----
    print("\n== 4. 打码效果（纯色/马赛克/模糊）==")
    from app.mask import (EFFECT_LABELS, MaskPlan, MaskRegion, build_filter,
                          is_complex_filter)
    try:
        for key, label in EFFECT_LABELS.items():
            app.var_effect.set(label)
            app.on_effect_change()
            check(f"效果 {key} 可选中", app._current_effect() == key,
                  app._current_effect())
            plan = MaskPlan(1920, 1080, True,
                            extra=[MaskRegion(170, 865, 190, 105)],
                            effect=key, intensity=int(app.var_strength.get()))
            flt = build_filter(plan)
            check(f"效果 {key} 生成滤镜", bool(flt), (flt or "")[:64])
            if key == "solid":
                check("纯色走 -vf", not is_complex_filter(flt))
                check("纯色隐藏强度滑条",
                      not app.sld_strength.winfo_manager())
            else:
                check(f"{key} 走 -filter_complex", is_complex_filter(flt))
                check(f"{key} 强度滑条可见",
                      app.sld_strength.winfo_manager() == "grid")
    except Exception as e:  # noqa: BLE001
        check("打码效果", False, f"{type(e).__name__}: {e}")

    # ---- 进度条 ----
    print("\n== 5. 分段进度条 ==")
    try:
        pr = app.progress
        pr.set_progress(0)
        check("0% 不填格", pr._filled_count() == 0, str(pr._filled_count()))
        pr.set_progress(50)
        check("50% 填一半", 10 <= pr._filled_count() <= 14, str(pr._filled_count()))
        pr.set_progress(100)
        check("100% 全满", pr._filled_count() == pr.segments,
              f"{pr._filled_count()}/{pr.segments}")
        pr.set_progress(120)
        check("超 100 被夹住", pr._filled_count() == pr.segments)
        pr.set_progress(-5)
        check("负值被夹到 0", pr._filled_count() == 0)
        pr.set_indeterminate(True)
        check("可切扫动模式", pr._indeterminate)
        pr.set_progress(40)
        check("设百分比后自动退出扫动", not pr._indeterminate)
        pr.reset()
        check("reset 归零", pr._pct == 0 and not pr._indeterminate)
    except Exception as e:  # noqa: BLE001
        check("进度条", False, f"{type(e).__name__}: {e}")

    # ---- 时间解析（时间段输入框仍需要，parse_hhmmss 已在上方覆盖）----

    # ---- 拖放 ----
    print("\n== 6. 文件拖放 ==")
    try:
        check("tkinterdnd2 已加载", gm._HAS_DND is True, str(gm._HAS_DND))
        if gm._HAS_DND:
            check("窗口注册了 drop_target", hasattr(app, "drop_target_register"))
        app.canvas_box.set_drop_highlight(True)
        check("拖入高亮可开", app.canvas_box._drop_hint is True)
        app.canvas_box.set_drop_highlight(False)
        check("拖入高亮可关", app.canvas_box._drop_hint is False)

        class _Ev:
            data = "{C:/definitely/not/here.h264}"
        try:
            app.on_drop(_Ev())
            check("拖入无效路径不崩溃", True)
        except Exception as e:  # noqa: BLE001
            check("拖入无效路径不崩溃", False, f"{type(e).__name__}: {e}")
    except Exception as e:  # noqa: BLE001
        check("拖放", False, f"{type(e).__name__}: {e}")

    try:
        app.destroy()
    except Exception:  # noqa: BLE001
        pass


def main() -> int:
    print("=" * 58)
    print("GUI 冒烟测试")
    print("=" * 58)
    scan_source()
    build_ui()
    ok = sum(_r)
    print(f"\n{'=' * 58}\n结果：{ok}/{len(_r)} 通过")
    return 0 if ok == len(_r) else 1


if __name__ == "__main__":
    raise SystemExit(main())
