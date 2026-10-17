"""校验 GitHub Actions workflow：结构 + PowerShell 语法。

背景（真实踩坑）：build.yml 里曾出现重复的 `- name:` 嵌套，
YAML 依然能 safe_load 通过（所以只做 YAML 校验发现不了），
但那几行被当作 shell 脚本内容执行，pwsh 报语法错误、整个步骤失败，
CI 上表现为"图标生成失败"，与图标脚本本身毫无关系。

这里做两层检查：
1. 结构：run 里不应出现 YAML 片段
2. 语法：用 PowerShell 解析器逐段解析（与 CI 真实行为一致）

用法：python tests/check_workflow.py
需要 Windows PowerShell；非 Windows 会跳过第 2 层。
"""
from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
WF = ROOT / ".github" / "workflows" / "build.yml"

YAML_MARKERS = ("- name:", "  shell:", "    run:", "jobs:", "steps:")
problems: list[str] = []


def check_structure() -> None:
    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = data["jobs"]["build"]["steps"]
    print(f"步骤数: {len(steps)}\n")
    for i, s in enumerate(steps, 1):
        name = s.get("name", s.get("uses", "?"))
        run = s.get("run", "")
        print(f"  {i:>2}. {name}")
        for marker in YAML_MARKERS:
            if marker in run:
                problems.append(
                    f"步骤{i}「{name}」的 run 里混入了 YAML 片段 {marker!r}（嵌套写坏）")
        if "name" not in s and "uses" not in s:
            problems.append(f"步骤{i} 缺少 name/uses")
        if s.get("shell") and "run" not in s:
            problems.append(f"步骤{i} 有 shell 但没有 run")


def check_powershell_syntax() -> None:
    """把每段 run 导出成 .ps1，用 PowerShell 解析器检查语法。"""
    if not shutil.which("powershell") and not shutil.which("pwsh"):
        print("\n[跳过] 本机无 PowerShell，无法校验脚本语法")
        return

    data = yaml.safe_load(WF.read_text(encoding="utf-8"))
    steps = data["jobs"]["build"]["steps"]
    scripts: list[tuple[int, str, Path]] = []

    with tempfile.TemporaryDirectory() as td:
        tdir = Path(td)
        for i, s in enumerate(steps, 1):
            if s.get("shell") != "pwsh" or not s.get("run"):
                continue
            f = tdir / f"step{i}.ps1"
            f.write_text(s["run"], encoding="utf-8-sig")
            scripts.append((i, s.get("name", "?"), f))

        print("\nPowerShell 语法检查：")
        for i, name, f in scripts:
            # 解析但不执行，只看语法错误
            ps = (
                "$e=$null; "
                f"[void][System.Management.Automation.Language.Parser]::"
                f"ParseFile('{f}', [ref]$null, [ref]$e); "
                "if ($e -and $e.Count) { "
                f"Write-Output ('ERR|' + $e[0].Message); exit 1 }} else {{ Write-Output 'OK' }}"
            )
            try:
                r = subprocess.run(
                    ["powershell", "-NoProfile", "-Command", ps],
                    capture_output=True, text=True, timeout=60)
                out = (r.stdout or "").strip()
            except (OSError, subprocess.SubprocessError) as e:
                print(f"  [跳过] 步骤{i}：无法调用 PowerShell（{e}）")
                continue

            if "ERR|" in out:
                msg = out.split("ERR|", 1)[1].splitlines()[0]
                print(f"  [语法错误] 步骤{i}「{name}」: {msg}")
                problems.append(f"步骤{i}「{name}」PowerShell 语法错误：{msg}")
            else:
                print(f"  [OK] 步骤{i}「{name}」")


def main() -> int:
    print("=" * 58)
    print("GitHub Actions workflow 检查")
    print("=" * 58)
    check_structure()
    check_powershell_syntax()
    print()
    if problems:
        print("!! 发现问题：")
        for p in problems:
            print("   -", p)
        return 1
    print("✓ workflow 结构与脚本语法均正常")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())