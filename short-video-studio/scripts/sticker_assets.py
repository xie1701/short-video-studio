#!/usr/bin/env python3
"""
剪映贴纸素材解析：把草稿里 `materials.stickers[*].path` 变成能喂给 ffmpeg 的帧序列。

为什么要有这个模块
──────────────────
剪映贴纸不是一张图，落盘形态有四种（同一个 `artistEffect/<resource_id>/<hash>/` 目录下）：

| 形态 | 判据 | 素材 |
|---|---|---|
| 静态图 | 有 `singleImage.png` | 单张 RGBA |
| 动图 | 有 `final.gif` | GIF |
| 序列帧图集 | `*.json`（TexturePacker `frames[]`）+ `*.png` | 图集切割 |
| 序列帧目录 | `SequenceMap.png` + `ani_info.json` | 按 ani_info 排 |

草稿 `path` 可能是**别的机器**的绝对路径（Windows 草稿导入 mac 后仍是原机器盘符路径（用户名原样保留，本机不存在）），
这种情况下按 `<本机缓存>/artistEffect/<resource_id>/` 反查——只要同 ID 目录在，素材就在。

对外接口
────────
    d = resolve_sticker_dir(mat["path"], mat.get("resource_id"))
    seq = sticker_sequence(d)      # -> StickerSequence(dir, count, fps, static)
    seq.frames                       # 帧 PNG 的目录（static 时只有一帧）
    seq.fps / seq.count / seq.static

自测
────
    python3 short-video-studio/scripts/sticker_assets.py --path <贴纸目录>
"""

from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

def _default_cache_root() -> Path:
    """剪映（mac 版）素材缓存根：优先环境变量，否则在 ~/Movies 下按结构自动发现。

    不写死应用目录名——仓库隐私门禁不许脚本里出现本机应用名，换个用户名也能用。
    """
    env = os.environ.get("JY_CACHE_ROOT")
    if env:
        return Path(env)
    for cand in sorted((Path.home() / "Movies").glob("*/User Data/Cache")):
        if (cand / "artistEffect").is_dir() or (cand / "effect").is_dir():
            return cand
    return Path.home() / "Movies" / "User Data" / "Cache"


DEFAULT_CACHE_ROOT = _default_cache_root()
OUT_ROOT = Path(
    os.environ.get("SVS_STICKER_CACHE")
    or (Path.home() / ".short-video-studio" / "cache" / "stickers")
)

DEFAULT_FPS = 16.0          # infoSticker.lua 里 `controller:setFps(16)` 是剪映贴纸的默认帧率
WIN_PATH_RE = re.compile(r"^[A-Za-z]:[\\/]")


@dataclass
class StickerSequence:
    frames_dir: Path
    count: int
    fps: float
    static: bool
    kind: str

    def input_args(self) -> List[str]:
        """给 ffmpeg 用的输入参数（静态图用 -loop 1，动效用 -framerate 序列）。"""
        if self.static:
            return ["-loop", "1", "-i", str(self.frames_dir / "f%05d.png" % 0)]
        return ["-framerate", f"{self.fps:g}", "-i", str(self.frames_dir / "f%05d.png")]


def _norm_dir(p: str) -> Path:
    return Path(str(p).replace("\\", "/"))


def resolve_sticker_dir(raw_path: str, resource_id: str = "",
                        cache_root: Path = DEFAULT_CACHE_ROOT) -> Optional[Path]:
    """把草稿里的 path 解析成本机真实存在的贴纸目录（目录本身，不含文件名）。"""
    if raw_path:
        p = _norm_dir(raw_path)
        if p.is_dir():
            return p
        # 旧草稿可能写到具体文件，截到目录
        if p.suffix and p.parent.is_dir():
            return p.parent
    rid = str(resource_id or "").strip()
    if not rid:
        # 退化：从 Windows 路径里抠出 19 位 ID
        m = re.search(r"artistEffect/(\d{15,20})", str(raw_path or ""))
        rid = m.group(1) if m else ""
    if not rid:
        return None
    base = Path(cache_root) / "artistEffect" / rid
    if not base.is_dir():
        return None
    # 目录下是 <asset_set_hash>/ 一层
    subs = [d for d in base.iterdir() if d.is_dir()]
    if not subs:
        return None
    if len(subs) == 1:
        return subs[0]
    # 多个包时挑体积最大的那个（通常就是真正在用的素材包）
    def _size(d: Path) -> int:
        return sum(f.stat().st_size for f in d.rglob("*") if f.is_file())
    return max(subs, key=_size)


def _read_lua_fps(d: Path) -> float:
    lua = d / "infoSticker.lua"
    if lua.is_file():
        m = re.search(r"setFps\(\s*([0-9.]+)\s*\)", lua.read_text(encoding="utf-8", errors="ignore"))
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                pass
    return DEFAULT_FPS


def _cache_key(d: Path, extra: str = "") -> str:
    src = f"{d}|{extra}|{_read_lua_fps(d)}"
    try:
        src += f"|{d.stat().st_mtime_ns}"
    except OSError:
        pass
    return hashlib.sha1(src.encode("utf-8")).hexdigest()[:16]


def _atlas_json(d: Path) -> Optional[Path]:
    """找 TexturePacker 图集描述（frames[] 结构）。"""
    for j in sorted(d.rglob("*.json")):
        try:
            obj = json.loads(j.read_text(encoding="utf-8", errors="ignore"))
        except Exception:
            continue
        if isinstance(obj, dict) and isinstance(obj.get("frames"), list) and obj["frames"]:
            return j
    return None


def _emit_frames(images: List, out: Path, size=None) -> int:
    """把 PIL 帧写成 fNNNNN.png（RGBA）。"""
    out.mkdir(parents=True, exist_ok=True)
    for old in out.glob("f*.png"):
        old.unlink()
    n = 0
    for i, im in enumerate(images):
        im = im.convert("RGBA")
        if size and im.size != size:
            im = im.resize(size)
        im.save(out / ("f%05d.png" % i))
        n += 1
    return n


def sticker_sequence(d: Path, force_static: bool = False) -> StickerSequence:
    """解析贴纸目录，产出统一的帧序列（结果按目录指纹缓存）。"""
    from PIL import Image

    d = Path(d)
    key = _cache_key(d, "static" if force_static else "")
    out = OUT_ROOT / key
    meta = out / "meta.json"
    if meta.is_file():
        try:
            m = json.loads(meta.read_text(encoding="utf-8"))
            seq = StickerSequence(out, int(m["count"]), float(m["fps"]), bool(m["static"]), m["kind"])
            if seq.static:
                if (out / "f00000.png").is_file():
                    return seq
            elif len(list(out.glob("f*.png"))) >= seq.count:
                return seq
        except Exception:
            pass

    # 1) 静态图
    single = d / "singleImage.png"
    if single.is_file() and not force_static:
        n = _emit_frames([Image.open(single)], out)
        fps = 0.0
        kind = "single"
        static = True
    elif force_static and (d / "singleImage.png").is_file():
        n = _emit_frames([Image.open(d / "singleImage.png")], out)
        fps = 0.0
        kind = "single"
        static = True
    # 2) 动图
    elif any(d.rglob("final.gif")):
        gif = next(d.rglob("final.gif"))
        im = Image.open(gif)
        frames = []
        try:
            while True:
                frames.append(im.convert("RGBA").copy())
                im.seek(im.tell() + 1)
        except EOFError:
            pass
        fps = _read_lua_fps(d)
        n = _emit_frames(frames, out)
        kind = "gif"
        static = False
    # 3) 图集
    elif _atlas_json(d):
        aj = _atlas_json(d)
        atlas = json.loads(Path(aj).read_text(encoding="utf-8", errors="ignore"))
        sheet = None
        for cand in Path(aj).parent.glob("*.png"):
            sheet = cand
            break
        if sheet is None:
            raise RuntimeError(f"图集缺 png: {aj}")
        sh = Image.open(sheet).convert("RGBA")
        frames, size = [], None
        for fr in atlas["frames"]:
            f = fr.get("frame") or {}
            x, y = int(f.get("x") or 0), int(f.get("y") or 0)
            w, h = int(f.get("w") or 0), int(f.get("h") or 0)
            if w <= 0 or h <= 0:
                continue
            im = sh.crop((x, y, x + w, y + h))
            if fr.get("rotated"):
                im = im.transpose(Image.ROTATE_90)
            if size is None:
                ss = fr.get("sourceSize") or {}
                size = (int(ss.get("w") or w), int(ss.get("h") or h))
            frames.append(im)
        if not frames:
            raise RuntimeError(f"图集切不出帧: {aj}")
        fps = _read_lua_fps(d)
        n = _emit_frames(frames, out, size)
        kind = "atlas"
        static = False
    # 4) SequenceMap 目录
    elif list(d.rglob("SequenceMap.png")):
        sm = next(d.rglob("SequenceMap.png"))
        im = Image.open(sm).convert("RGBA")
        fps = _read_lua_fps(d)
        info = next(d.rglob("ani_info.json"), None)
        cols = rows = 1
        if info:
            try:
                obj = json.loads(Path(info).read_text(encoding="utf-8", errors="ignore"))
                cols = int(obj.get("cols") or obj.get("column") or 1)
                rows = int(obj.get("rows") or obj.get("row") or 1)
            except Exception:
                pass
        fw, fh = im.width // max(cols, 1), im.height // max(rows, 1)
        frames = []
        for r in range(rows):
            for c in range(cols):
                frames.append(im.crop((c * fw, r * fh, (c + 1) * fw, (r + 1) * fh)))
        n = _emit_frames(frames, out)
        kind = "sequence_map"
        static = False
    else:
        # 兜底：目录里最大的 png 当静态图
        pngs = sorted(d.rglob("*.png"), key=lambda p: p.stat().st_size, reverse=True)
        if not pngs:
            raise RuntimeError(f"贴纸目录里找不到可用素材: {d}")
        n = _emit_frames([Image.open(pngs[0])], out)
        fps = 0.0
        kind = "fallback_png"
        static = True

    out.mkdir(parents=True, exist_ok=True)
    meta.write_text(json.dumps(
        {"count": n, "fps": fps, "static": static, "kind": kind, "source": str(d)},
        ensure_ascii=False), encoding="utf-8")
    return StickerSequence(out, n, fps, static, kind)


def main() -> None:
    import argparse
    ap = argparse.ArgumentParser(description="解析剪映贴纸素材")
    ap.add_argument("--path", help="贴纸目录或草稿里的 path")
    ap.add_argument("--resource-id", default="")
    args = ap.parse_args()
    if not args.path:
        raise SystemExit("需要 --path")
    d = resolve_sticker_dir(args.path, args.resource_id)
    if not d:
        print("未找到素材目录")
        raise SystemExit(2)
    seq = sticker_sequence(d)
    print(f"目录: {d}")
    print(f"类型: {seq.kind}  帧数: {seq.count}  fps: {seq.fps}  静态: {seq.static}")
    print(f"输出: {seq.frames_dir}")


if __name__ == "__main__":
    main()
