#!/usr/bin/env python3
"""
跨平台可运行性：真正会崩的地方，在跑之前就拦住。

用户问的原话
────────────
「这套 skill 是否能运行在 intel 版的 mac、x86 或 arm 版的 windows？还有 linux 下是否可以？
目标是可以通用嘛，看看还缺什么。」

审计结论（2026-11-23，五个平台逐条挂证据）
────────────────────────────────────────
不靠 Python 分支决定能不能跑——脚本里只有三处 `platform.system()=="Darwin"`
（`vision_inspector`、`person_matting`），没有一处架构分支。真正决定能不能跑的是**外部二进制**：

* 渲染链路（Pillow 逐帧 PNG + `eq/curves/lut3d/xfade`）**全平台可移植**，这是最大的资产；
* 会**直接崩**的只有四件事：没有 ffmpeg（或 ffmpeg < 5.1 不认 `-fps_mode`）、
  没有 edge-tts 又没有 macOS 的 `say`、没有 faster-whisper 又没有 `--script-json`、没有浏览器做 HTML 动效；
* Apple Vision 三件套（OCR / 人脸 / 人像分割）在非 mac 上是**静默降级**：人脸构图退回居中、
  封面不选脸、黑盒质检三项转人工签字——不崩，但你要知道它降级了。

这个模块做三件事，都很小：

1. `ffmpeg_capabilities()` —— 问清楚这台机器的 ffmpeg 是几版、有没有 `-fps_mode`，
   低于 5.1 时渲染命令自动换成 `-vsync`，而不是跑到第一步才报 `Unrecognized option`。
2. `probe_platform()` —— 给一份「这台机器能跑到哪一步、哪几步会静默降级」的体检单。
3. `explain_degradation(feature)` —— 大白话告诉你降级之后成片会差在哪，不是一句 `None`。
"""

from __future__ import annotations

import json
import os
import platform
import re
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Optional

_CAPS_CACHE: Dict[str, Any] = {}

# 各平台「能跑到哪一步」的实话（与审计结论一一对应，改代码时要同步改这里）
DEGRADE_NOTES = {
    "vision_ocr": "这台机器没有 Apple Vision：OCR 质检会转人工，代码视频里认不出字",
    "vision_face": "这台机器没有 Apple Vision：小窗取景退回居中，人物脸可能被切到边",
    "vision_segment": "这台机器没有 Apple Vision：封面不会自动切人像，用整帧",
    "matting": "这台机器没有人像抠像：需要抠像的风格（05/07/19）会改成普通画面叠放",
    "tts": "这台机器既没有 edge-tts 也没有 macOS 的 say：纯配音路线得自己提供录音",
    "asr": "这台机器装不上 faster-whisper：自动转写用不了，请改用 --script-json 逐句稿",
    "browser": "这台机器找不到 Chrome/Edge：HTML 动效补镜会退回静态图",
}


def _run_ffmpeg_version(bin_path: str) -> Optional[str]:
    try:
        res = subprocess.run([bin_path, "-version"], capture_output=True, text=True, timeout=20)
    except Exception:
        return None
    if res.returncode != 0:
        return None
    first = (res.stdout or "").splitlines()
    return first[0].strip() if first else ""


def ffmpeg_capabilities(ffmpeg_path: Optional[str] = None) -> Dict[str, Any]:
    """这台机器的 ffmpeg 到底什么版本、认不认 `-fps_mode`。

    离线包里塞的是一个 Homebrew 的**动态链接**二进制（只有 426 KB，`otool -L` 全是
    `/opt/homebrew/Cellar/...`），换到没装同版本 Homebrew 的机器上会 dyld 直接失败。
    所以这里除了查版本，还要判断 vendor 里那个是不是真能跑——跑不动就回落 PATH。
    """
    key = str(ffmpeg_path or "")
    if key in _CAPS_CACHE:
        return _CAPS_CACHE[key]

    cands: List[str] = []
    if ffmpeg_path:
        cands.append(str(ffmpeg_path))
    else:
        from common import find_bin
        for name in ("ffmpeg",):
            p = find_bin(name)
            if p:
                cands.append(p)
        which = shutil.which("ffmpeg")
        if which:
            cands.append(which)

    caps: Dict[str, Any] = {"path": "", "version": "", "major_minor": None,
                            "fps_mode": False, "usable_bins": [], "vendor_bin_broken": False}
    for c in cands:
        if not (c and os.path.isfile(c) and os.access(c, os.X_OK)):
            continue
        line = _run_ffmpeg_version(c)
        if not line:
            # vendor 里的二进制存在但一跑就死（架构不对 / dylib 缺失）
            if "vendor" in c.replace("\\", "/"):
                caps["vendor_bin_broken"] = True
            continue
        caps["usable_bins"].append(c)
        if not caps["path"]:
            caps["path"] = c
            caps["version"] = line
            m = re.search(r"ffmpeg version (\d+)\.(\d+)", line)
            if m:
                caps["major_minor"] = (int(m.group(1)), int(m.group(2)))
                caps["fps_mode"] = caps["major_minor"] >= (5, 1)
    _CAPS_CACHE[key] = caps
    return caps


def fps_mode_args(mode: str = "cfr") -> List[str]:
    """`-fps_mode cfr` 只有 ffmpeg ≥ 5.1 认；Ubuntu 22.04 自带 4.4 用老参数 `-vsync`。

    mode: "cfr"（恒定帧率）或 "passthrough"（不补不丢）
    """
    caps = ffmpeg_capabilities()
    if caps.get("fps_mode"):
        return ["-fps_mode", mode]
    legacy = "cfr" if mode == "cfr" else "passthrough"
    return ["-vsync", legacy]


def probe_platform() -> Dict[str, Any]:
    """给用户一张实话体检单：这台机器能跑到哪、哪几步会静默降级。"""
    caps = ffmpeg_capabilities()
    is_mac = platform.system() == "Darwin"
    has_swiftc = bool(shutil.which("swiftc") or shutil.which("xcrun"))
    features: Dict[str, Dict[str, Any]] = {}

    def _mark(name: str, ok: bool, why: str = "") -> None:
        features[name] = {"usable": bool(ok), "note": "" if ok else (why or DEGRADE_NOTES.get(name, ""))}

    _mark("ffmpeg", bool(caps.get("path")),
          "找不到能执行的 ffmpeg（离线包里的那个可能是别的架构，或没装）")
    _mark("vision_ocr", is_mac and has_swiftc)
    _mark("vision_face", is_mac and has_swiftc)
    _mark("vision_segment", is_mac and has_swiftc)
    _mark("matting", is_mac and has_swiftc)

    import importlib.util
    _mark("asr", importlib.util.find_spec("faster_whisper") is not None)
    has_edge = importlib.util.find_spec("edge_tts") is not None
    _mark("tts", has_edge or (is_mac and bool(shutil.which("say"))))
    chrome = next((p for p in (
        shutil.which("google-chrome"), shutil.which("chromium"), shutil.which("chrome"),
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    ) if p and (os.path.isfile(p) or shutil.which(str(p)))), "")
    _mark("browser", bool(chrome))

    degradations = {k: v["note"] for k, v in features.items() if not v["usable"] and v.get("note")}
    return {
        "platform": f"{platform.system()} {platform.machine()}",
        "python": platform.python_version(),
        "ffmpeg": {k: caps.get(k) for k in ("path", "version", "major_minor", "fps_mode", "vendor_bin_broken")},
        "features": features,
        "degradations": degradations,
        "hard_blocks": [k for k, v in features.items() if not v["usable"] and k in ("ffmpeg",)],
        "verdict": ("能跑完整链路" if not degradations else
                    f"能跑，但有 {len(degradations)} 项会降级或需你自备"),
    }


def cli(argv: Optional[List[str]] = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(description="跨平台体检：这台机器能跑到哪一步、哪几步会降级")
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args(argv)
    rep = probe_platform()
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
        return 0
    ff = rep["ffmpeg"]
    print(f"平台：{rep['platform']} · Python {rep['python']}")
    print(f"ffmpeg：{ff.get('version') or '未找到'}  (-fps_mode：{'有' if ff.get('fps_mode') else '没有，自动用 -vsync'})")
    if ff.get("vendor_bin_broken"):
        print("注意：离线包里自带的 ffmpeg 在这台机器上跑不起来（多半是架构或 dylib 不匹配），已回落到 PATH")
    print(f"结论：{rep['verdict']}")
    for k, note in rep["degradations"].items():
        print(f"  · 降级 {k}：{note}")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    raise SystemExit(cli())
