#!/usr/bin/env python3
"""
维护工具：生成通用 17x17x17 3D 调色 LUT（warm_store.cube / clean_creator.cube）。
注：BGM 与真实拟音 SFX 已全面切换为 assets/real_bgm/ 与 assets/vibe_sound/（外加脚本按需合成的 15 种无版权动效拟音），
本工具仅负责重建通用兜底 3D LUT。
"""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPTS_ROOT = Path(__file__).resolve().parents[1] / "short-video-studio" / "scripts"
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import ASSETS_DIR, maybe_reexec_in_venv, write_cube_lut


def generate_luts_all() -> None:
    """使用向量化与共享 common.write_cube_lut 生成通用 3D LUT。"""
    import numpy as np

    size = 17
    grid = np.linspace(0.0, 1.0, size, dtype=np.float32)
    bb, gg, rr = np.meshgrid(grid, grid, grid, indexing="ij")
    r, g, b = rr.reshape(-1), gg.reshape(-1), bb.reshape(-1)

    def s_curve(v: np.ndarray, c: float) -> np.ndarray:
        return 0.5 + (v - 0.5) * c

    write_cube_lut(
        ASSETS_DIR / "luts" / "warm_store.cube",
        np.stack([s_curve(r * 1.05 + 0.02, 1.06), s_curve(g * 1.02 + 0.01, 1.05), s_curve(b * 0.94, 1.04)], axis=-1),
        size,
        "Warm Store",
    )
    write_cube_lut(
        ASSETS_DIR / "luts" / "clean_creator.cube",
        np.stack([s_curve(r * 1.01, 1.07), s_curve(g * 1.02 + 0.005, 1.07), s_curve(b * 1.04 + 0.01, 1.07)], axis=-1),
        size,
        "Clean Creator",
    )


def main() -> None:
    print("[assets] 正在生成内置通用 3D LUTs...")
    generate_luts_all()
    print("[assets] 通用 3D LUT 资产已就绪。")


if __name__ == "__main__":
    maybe_reexec_in_venv()
    main()
