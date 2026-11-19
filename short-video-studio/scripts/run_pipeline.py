#!/usr/bin/env python3
"""
short-video-studio 专业制片流水线编排器
支持 5 种工作模式：
1. full           : 跑通 Step 1 -> Step 9 -> 渲染成片 -> 导出剪映/HyperFrames -> 22项自动+黑盒质检 -> 完工清理
2. lock-preview   : 停在打断#3，输出锁定音轨与低清粗剪预览 (output_preview.mp4)
3. reedit-visuals : [打断#3 分支A] 严格保持 Step 3 锁定音轨不变（如 01_normalized 已被 tidy 自动按需重建），重排 Step 4-9 + 出片 + 22项质检
4. relock-voice   : [打断#3 分支B] 回到 Step 2/3 重新锁定音轨，再重排 Step 4-9 + 出片 + 22项质检
5. render-locked  : 校验 approval_lock 指纹锁后直接按锁定方案终稿渲染、导出与 22 项质检
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (
    ASSETS_DIR,
    DEFAULT_FPS,
    maybe_reexec_in_venv,
    read_json,
    resolve_workspace_paths,
    write_json,
)

maybe_reexec_in_venv()

from PIL import Image, ImageDraw
from apply_style_spectrum import resolve_style_runtime_config
from export_hyperframes import export_hyperframes_draft
from export_jianying import export_jianying_draft
from generate_code_broll import generate_demo_media_pack
from inventory_media import (
    MEDIA_EXTS,
    build_shot_log_dict,
    inventory_directory,
    render_markdown_summary,
)
from qa_inspector import inspect_video
from raw_intel_ingest import lint_script, render_lint_markdown
from render_mp4 import _fit_line, _get_cached_font, render_mp4
from step1_normalize import run_step1
from step2_transcribe import run_step2
from step3_lock_audio import run_step3
from step4_build_edl import run_step4
from step5_subtitles import run_step5
from step6_packaging import run_step6
from step7_color_grade import run_step7
from step8_mix_audio import run_step8
from step9_title_cover import run_step9
from workflow_guard import tidy_workspace, verify_lock


def _run_script_prelint(
    script_path: Path,
    pipeline_dir: Path,
    raw_intel: Optional[Dict[str, Any]] = None,
) -> None:
    raw = read_json(script_path)
    items = raw.get("sentences") or raw.get("segments") or [] if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return
    intel = raw_intel or {"utterance_count": 0, "verified_by_real_users": False, "pain_keywords": [], "proof_assets": []}
    lint_res = lint_script(items, raw_intel=intel)
    (pipeline_dir / "00_script_lint.md").write_text(render_lint_markdown(lint_res, intel), encoding="utf-8")
    if lint_res["error_count"] > 0 or lint_res["warn_count"] > 0:
        print(
            f"[pre-lint] 口语脚本体检提示: Error={lint_res['error_count']}, "
            f"Warn={lint_res['warn_count']} -> {pipeline_dir / '00_script_lint.md'}"
        )


def _generate_hook_variants(pipeline_dir: Path, count: int) -> Optional[Path]:
    """根据已生成的标题/封面备选方案，产出多版本开篇钩子对照方案与变体封面图。"""
    n_var = max(2, min(3, int(count)))
    cov_p = pipeline_dir / "09_title_cover.json"
    if not cov_p.is_file():
        return None
    cov = read_json(cov_p)
    titles = list(cov.get("titles") or [])
    base_jpg = Path(cov.get("cover_jpg") or (pipeline_dir / "09_cover.jpg"))
    subs = read_json(pipeline_dir / "05_subtitles.json") if (pipeline_dir / "05_subtitles.json").exists() else {}
    font_path = subs.get("font_path")

    angles = ["颠覆对比钩子", "数字痛点钩子", "悬念反问钩子"]
    variants: List[Dict[str, Any]] = []
    for idx in range(n_var):
        title_txt = titles[idx] if idx < len(titles) else (titles[0] if titles else str(cov.get("cover_main", "核心真相")))
        out_img = pipeline_dir / f"09_cover_v{idx + 1}.jpg"
        if base_jpg.is_file():
            with Image.open(base_jpg) as im:
                canvas = im.convert("RGBA")
                draw = ImageDraw.Draw(canvas)
                draw.rounded_rectangle([(84, 152), (620, 224)], radius=14, fill=(15, 23, 42, 228), outline=(250, 204, 21, 220), width=2)
                fnt, fitted = _fit_line(draw, f"{angles[idx]}：{title_txt[:14]}", font_path, 500, 28, 20)
                draw.text((104, 172), fitted, font=fnt, fill=(250, 204, 21, 255))
                canvas.convert("RGB").save(out_img, quality=92)
        variants.append({
            "variant_id": f"hook_v{idx + 1}",
            "angle": angles[idx],
            "headline": title_txt,
            "cover_jpg": str(out_img.resolve()) if out_img.is_file() else None,
        })

    out_json = pipeline_dir / "09_hook_variants.json"
    write_json(out_json, {"count": len(variants), "variants": variants})
    return out_json


def write_decisions_made(pipeline_dir: Path, track: str) -> Path:
    """生成《我替你做的决定》专业默认值清单，方便用户一句话直接修改。"""
    mf = read_json(pipeline_dir / "01_manifest.json") if (pipeline_dir / "01_manifest.json").exists() else {}
    ae = read_json(pipeline_dir / "03_audio_events.json")
    edl = read_json(pipeline_dir / "04_edl.json")
    sub = read_json(pipeline_dir / "05_subtitles.json")
    pkg = read_json(pipeline_dir / "06_packaging.json")
    col = read_json(pipeline_dir / "07_color_plan.json")
    mix = read_json(pipeline_dir / "08_mix_metrics.json")
    cov = read_json(pipeline_dir / "09_title_cover.json") if (pipeline_dir / "09_title_cover.json").exists() else {}
    qa = read_json(pipeline_dir / "10_qa_report.json") if (pipeline_dir / "10_qa_report.json").exists() else {}
    jy = read_json(pipeline_dir / "jianying_export_status.json") if (pipeline_dir / "jianying_export_status.json").exists() else {}
    hf = read_json(pipeline_dir / "hyperframes_export_status.json") if (pipeline_dir / "hyperframes_export_status.json").exists() else {}

    hdr_cnt = int(mf.get("hdr_tonemapped_count", 0))
    rt_src = "原片实录房间底噪" if ae.get("room_tone_source") == "extracted_real" else "柔和合成空气声"
    p_range = ae.get("pause_range_sec", [ae.get("pause_duration_sec", 0.2), ae.get("pause_duration_sec", 0.2)])
    best_cov = cov.get("best_frame_meta", {})

    lines = [
        "# 我替你做的决定（专业默认配置与一句话修改指南）",
        "",
        "为了让创作尽量少打断你，除核心选题、素材确认和音轨粗剪预览外，其余后期与播控细节已按行业专业标准自动完成：",
        "",
        "| 环节 | 我替你做的专业决定 | 如何一句话修改 |",
        "| :--- | :--- | :--- |",
        f"| **适用轨道** | `{'门店轨 (同城转化)' if track == 'store' else '博主轨 (留存涨粉)'}` | “改按博主轨/门店轨风格重出” |",
        f"| **HDR 色彩还原** | 已检测并执行 Hable Rec.709 映射 `{hdr_cnt}` 条素材 | “保留原始对比/调柔和一点” |",
        f"| **BGM 与强拍对齐** | `{Path(ae['bgm']['file']).name}` ({ae['bgm']['bpm']} BPM)，钩子首字对齐 0.00s 强拍 | “换一首更舒缓/更燃的 BGM” |",
        f"| **呼吸停顿与房底** | 动态呼吸停顿 `{p_range[0]}s–{p_range[1]}s` + `{rt_src}` + 过零点精剪 | “把句间停顿整体改紧凑/舒缓一点” |",
        f"| **J-cut 与锁眼推拉** | 真 J-cut `{edl.get('j_cut_applied_count', 0)}` 处（默认 `{edl['j_cut_default_frames']} 帧`），锁眼微推拉 `{edl.get('punch_in_count', 0)}` 处 | “把 J-cut 改成 0/2/4 帧”或“关闭微推拉” |",
        f"| **字幕样式与避脸** | `{sub['template_name']}` (`{sub['template_id']}`)，商用字体 `{Path(sub['font_path']).name if sub.get('font_path') else '默认'}` + 自动避脸 | “字幕换成其他模板” |",
        f"| **动效包装密度** | 音效 {pkg['budget']['used_sfx_cues']}/{pkg['budget']['max_sfx_cues']} 个，视觉包装 {pkg['budget']['used_visual_elements']}/{pkg['budget']['max_visual_elements']} 个 | “把花字贴纸再精简一点” |",
        f"| **肤色保护调色** | Hero基准锚定 + 皮肤矢量校验 + `{int(round(col.get('lut_strength', 0.62)*100))}%` 强度 `{col['lut_name']}` | “调色浓一点(85%)/淡一点(40%)或换 LUT” |",
        f"| **广播级混音** | 广播人声链 + 250-4kHz 双频段闪避，响度 `{mix['measured_lufs']:.2f} LUFS` / LRA `{mix.get('lra_lu', 0):.1f} LU` / SNR `{mix.get('voice_bgm_snr_db', 0):.1f}dB` | “BGM 音量再小一点 / 大一点” |",
        f"| **黄金封面优选** | 综合选帧分 `{best_cov.get('score', 0)}` ({best_cov.get('detail', '自动优选')})，输出 9:16 首帧 + 3:4 主页封面 | “换一帧做封面/改封面大字” |",
        f"| **22项自动+黑盒质检** | `{qa.get('pass_count', 0)}/{qa.get('total_checks', 22)}` 项 PASS（状态：`{qa.get('approval_status', 'PENDING')}`） | 查看 `10_qa_report.md` |",
        f"| **双端可编辑工程** | 高清成片 `output_final.mp4` + 剪映 `{jy.get('status', 'unknown')}` + HyperFrames `{hf.get('status', 'unknown')}` | “导出/修改剪映或 HyperFrames 工程” |",
        "",
    ]
    out_md = pipeline_dir / "decisions_made.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


def write_review_player_html(pipeline_dir: Path) -> Path:
    """生成纯静态零后端离线时间线打点审片器 review_player.html（纯 DOM 操作防 XSS，读取真实 fps）。"""
    edl = read_json(pipeline_dir / "04_edl.json") if (pipeline_dir / "04_edl.json").exists() else {}
    fps = int(edl.get("fps") or DEFAULT_FPS)
    html_str = f"""<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>离线时间线打点审片器 · short-video-studio</title>
<style>
  body {{ font-family: -apple-system, sans-serif; background: #0b0f17; color: #f8fafc; margin: 20px; display: flex; gap: 24px; flex-wrap: wrap; }}
  video {{ width: 340px; border-radius: 14px; border: 1px solid #334155; background: #000; }}
  .panel {{ flex: 1; min-width: 300px; background: #121926; padding: 18px; border-radius: 14px; border: 1px solid #25344d; }}
  button {{ background: #facc15; color: #0f172a; border: none; padding: 8px 14px; border-radius: 8px; font-weight: 700; cursor: pointer; margin-right: 8px; }}
  input {{ width: 70%; padding: 8px; border-radius: 8px; border: 1px solid #334155; background: #0b0f17; color: #fff; }}
  ul {{ list-style: none; padding: 0; margin-top: 14px; }}
  li {{ padding: 8px 10px; background: #1e293b; border-radius: 8px; margin-bottom: 6px; cursor: pointer; }}
</style>
</head>
<body>
  <video id="v" controls playsinline src="output_final.mp4"></video>
  <div class="panel">
    <h3>成片离线时间线批注（按 M 键抓取秒数 · {fps}fps）</h3>
    <div>
      <input id="note" placeholder="输入修改意见，如：第4秒字幕挡脸 / 这句再快一点" />
      <button type="button" onclick="addMark()">+ 在当前帧打点</button>
      <button type="button" onclick="exportJson()" style="background:#38bdf8;">导出 annotations.json</button>
    </div>
    <ul id="list"></ul>
  </div>
<script>
  const FPS = {fps};
  const v = document.getElementById('v');
  const marks = [];
  function render() {{
    const ul = document.getElementById('list');
    ul.textContent = '';
    marks.forEach((m) => {{
      const li = document.createElement('li');
      li.addEventListener('click', () => {{ v.currentTime = m.time_sec; }});
      const b = document.createElement('b');
      b.textContent = '@' + m.time_sec.toFixed(2) + 's (f' + m.frame + ')';
      li.appendChild(b);
      li.appendChild(document.createTextNode(' — ' + m.note));
      ul.appendChild(li);
    }});
  }}
  function addMark() {{
    const t = Number((v.currentTime || 0).toFixed(2));
    const el = document.getElementById('note');
    const note = el.value.trim() || '此处需要微调';
    marks.push({{ time_sec: t, frame: Math.round(t * FPS), note }});
    marks.sort((a, b) => a.time_sec - b.time_sec);
    el.value = '';
    render();
  }}
  function exportJson() {{
    const blob = new Blob([JSON.stringify(marks, null, 2)], {{ type: 'application/json' }});
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'annotations.json';
    a.click();
  }}
  window.addEventListener('keydown', (e) => {{
    if ((e.key === 'm' || e.key === 'M') && document.activeElement.tagName !== 'INPUT') {{
      document.getElementById('note').focus();
    }}
  }});
</script>
</body>
</html>
"""
    p = pipeline_dir / "review_player.html"
    p.write_text(html_str, encoding="utf-8")
    return p


def write_delivery_summary(
    pipeline_dir: Path,
    ws: Dict[str, Any],
    track: str,
    qa_report: Dict[str, Any],
) -> Path:
    """输出 11_delivery_summary.md（复用 09_title_cover.json 真实标题，绝不再造另一套）。"""
    cov = read_json(pipeline_dir / "09_title_cover.json") if (pipeline_dir / "09_title_cover.json").exists() else {}
    titles = list(cov.get("titles") or [])
    if not titles:
        ae = read_json(pipeline_dir / "03_audio_events.json") if (pipeline_dir / "03_audio_events.json").exists() else {}
        sents = ae.get("sentences") or []
        core = str(sents[0].get("text", "核心分享"))[:16] if sents else "核心分享"
        titles = [core]

    hv_p = pipeline_dir / "09_hook_variants.json"
    hv_note = ""
    if hv_p.is_file():
        hv = read_json(hv_p)
        hv_note = f"\n- **多钩子封面变体**：已生成 {hv.get('count', 0)} 套对比封面（`09_hook_variants.json` / `09_cover_v1..jpg`）"

    lines = [
        f"# 成品交付清单 · 账号 `{ws.get('account_slug', 'default')}` · 项目 `{ws.get('project_id', pipeline_dir.parent.name)}`",
        "",
        f"- **隔离加工工作区**：`{ws.get('project_dir', pipeline_dir.parent)}`（原素材目录 `{ws.get('raw_dir', '')}` 全程只读零修改）",
        f"- **22 项自动+黑盒质检**：`{qa_report.get('pass_count', 0)}/{qa_report.get('total_checks', 22)}` PASS | 状态：`{qa_report.get('approval_status', 'MACHINE_PASS_PENDING_HUMAN')}`{hv_note}",
        "",
        "## 一、 最终交付物（同步于 `out/` 目录）",
        "- 成片 MP4：`out/output_final.mp4`",
        "- 双封面图：`out/09_cover.jpg`（信息流首帧 9:16） / `out/09_cover_3x4.jpg`（主页网格 3:4）",
        "- 排画前盲听音轨：`out/03_blind_audio.m4a`",
        "- 素材可视化接触表：`out/00_contact_sheet.jpg`",
        "- 合规审校 & SHA-256 授权清单：`out/00_compliance_report.md` · `out/asset_license_manifest.json`",
        "- 离线打点审片器：`out/review_player.html`",
        "- 双宿主可编辑工程：`pipeline/jianying_draft/timeline.fcpxml` / `pipeline/hyperframes_draft/index.html`",
        "",
        "## 二、 推荐发布标题（与 `09_title_cover.json` 完全同源）",
        *[f"{i}. {t}" for i, t in enumerate(titles, start=1)],
        "",
        "## 三、 人工终审签字",
        "- 详细默认参数与修改口令见 `decisions_made.md`。",
        f"  `python scripts/workflow_guard.py approve-final --pipeline-dir \"{pipeline_dir}\" --note \"三项终审通过\"`",
        "",
        "## 四、 发布后 48 小时数据闭环",
        f"  `python scripts/content_log_review.py --account \"{ws.get('account_slug', 'default')}\" --workspace-root \"{ws.get('workspace_root', '')}\" --add-json '<json>'`",
        "",
    ]
    out_md = pipeline_dir / "11_delivery_summary.md"
    out_md.write_text("\n".join(lines), encoding="utf-8")
    return out_md


def _rebuild_normalized_if_missing(pipeline_dir: Path) -> None:
    """tidy 清理过 01_normalized 后，若以 reedit-visuals 模式返工，自动从 01_manifest.json 源路径重建。"""
    man_p = pipeline_dir / "01_manifest.json"
    if not man_p.is_file():
        return
    man = read_json(man_p)
    items = man.get("items") or []
    missing = [it for it in items if it.get("normalized_mp4") and not Path(it["normalized_mp4"]).is_file()]
    if not missing:
        return
    src_paths: List[Path] = []
    seen: set[str] = set()
    for it in items:
        sp = str(it.get("source_path") or "")
        if sp and sp not in seen and Path(sp).is_file():
            seen.add(sp)
            src_paths.append(Path(sp))
    if not src_paths:
        raise FileNotFoundError("01_normalized 已被清理且找不到原始 source_path 源文件，无法重建。")
    print(f"[pipeline] 检测到 {len(missing)} 条已清理的 01_normalized，正从只读源素材按需重建...")
    run_step1(src_paths, pipeline_dir)


def _render_and_verify_only(
    pipeline_dir: Path,
    track: str,
    benchmark: Optional[Path],
    simulate_jy_fail: bool,
    simulate_hf_fail: bool,
    dialect: str,
    store_anchor: Optional[str],
    require_lock: bool,
    strict_asr: bool,
    hook_variants: int,
    ws: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if require_lock:
        code, msg, _ = verify_lock(pipeline_dir)
        if code != 0:
            raise RuntimeError(f"哈希锁校验失败，阻断正式渲染：{msg}")

    final_mp4 = render_mp4(pipeline_dir, preview=False)
    jy_status = export_jianying_draft(pipeline_dir, simulate_failure=simulate_jy_fail)
    hf_status = export_hyperframes_draft(pipeline_dir, simulate_failure=simulate_hf_fail)
    if hook_variants > 1:
        _generate_hook_variants(pipeline_dir, hook_variants)

    qa_report = inspect_video(
        pipeline_dir,
        video_path=final_mp4,
        benchmark_path=benchmark,
        dialect=dialect,
        store_anchor=store_anchor,
        strict_asr=strict_asr,
    )
    decisions_md = write_decisions_made(pipeline_dir, track)
    write_review_player_html(pipeline_dir)
    delivery_md = write_delivery_summary(pipeline_dir, ws or {}, track, qa_report)

    return {
        "final_mp4": str(final_mp4),
        "jianying": jy_status,
        "hyperframes": hf_status,
        "qa_report": qa_report,
        "decisions_made_md": str(decisions_md),
        "delivery_summary_md": str(delivery_md),
    }


def run_visual_to_delivery(
    pipeline_dir: Path,
    track: str,
    j_cut_frames: int,
    subtitle_template: Optional[str],
    lut_name: Optional[str],
    prefer_broll_only: bool,
    benchmark: Optional[Path],
    simulate_jy_fail: bool,
    simulate_hf_fail: bool = False,
    preview_only: bool = False,
    style_id: Optional[str] = None,
    account_profile: Optional[Dict[str, Any]] = None,
    dialect: str = "mandarin",
    store_anchor: Optional[str] = None,
    require_lock: bool = False,
    strict_asr: bool = False,
    licenses_path: Optional[Path] = None,
    hook_variants: int = 1,
    ws: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run_step4(
        pipeline_dir,
        track=track,
        j_cut_frames=j_cut_frames,
        prefer_broll_only=prefer_broll_only,
        style_id=style_id,
        account_profile=account_profile,
    )
    run_step5(pipeline_dir, track=track, template_id=subtitle_template)
    run_step6(pipeline_dir, style_id=style_id)
    run_step7(pipeline_dir, track=track, lut_name=lut_name, style_id=style_id)
    run_step8(
        pipeline_dir,
        licenses_path=licenses_path,
        out_dir=ws["out_dir"] if ws else None,
    )
    run_step9(pipeline_dir, track=track)

    if preview_only:
        preview_mp4 = render_mp4(pipeline_dir, preview=True)
        return {"preview_mp4": str(preview_mp4)}

    return _render_and_verify_only(
        pipeline_dir=pipeline_dir,
        track=track,
        benchmark=benchmark,
        simulate_jy_fail=simulate_jy_fail,
        simulate_hf_fail=simulate_hf_fail,
        dialect=dialect,
        store_anchor=store_anchor,
        require_lock=require_lock,
        strict_asr=strict_asr,
        hook_variants=hook_variants,
        ws=ws,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description="short-video-studio 专业端到端流水线编排器 (支持多账号与专属工作区隔离)")
    parser.add_argument("--project-dir", help="可选：自定义加工项目目录（未传时自动落在 --workspace-root 下隔离目录）")
    parser.add_argument("--raw-dir", help="原始素材目录（严格只读，绝不向此目录写入任何加工文件）")
    parser.add_argument("--account", help="账号标识，自动隔离该账号参数基因/合规白名单/复盘日志/项目目录")
    parser.add_argument("--project-id", help="项目标识")
    parser.add_argument("--workspace-root", help="专属加工工作区根目录")
    parser.add_argument("--track", choices=["store", "creator"], default="store")
    parser.add_argument(
        "--mode",
        choices=["full", "lock-preview", "reedit-visuals", "relock-voice", "render-locked"],
        default="full",
        help="执行模式",
    )
    parser.add_argument("--script-json", help="口播或配音台词 JSON 文件")
    parser.add_argument("--force-tts", action="store_true", help="强制使用文案生成 TTS 配音")
    parser.add_argument("--bgm", help="自定义 BGM 文件路径")
    parser.add_argument("--pause-sec", type=float, default=0.20, help="基准句间停顿 (0.14-0.32s)")
    parser.add_argument("--j-cut-frames", type=int, default=None, help="句间 J-cut 声先入帧数 (默认 3，或由 --style-id 决定)")
    parser.add_argument("--style-id", help="可选：套用风格谱模板 ID (style_01 ~ style_20)")
    parser.add_argument("--subtitle-template", help="字幕模板 ID")
    parser.add_argument("--lut", help="调色 LUT 文件名")
    parser.add_argument("--prefer-broll-only", action="store_true", help="改画模式：尽量切换为纯 B-roll/代码补镜")
    parser.add_argument("--benchmark", help="可选：对标视频路径")
    parser.add_argument("--raw-intel", help="可选：一手原材萃取 00_raw_intel.json 路径")
    parser.add_argument("--visual-index", help="可选：外部高精度镜头语义索引 00_visual_index.json 路径")
    parser.add_argument("--account-profile", help="可选：账号参数基因 account_profile.json 路径")
    parser.add_argument("--whitelist", help="可选：账号合规白名单 compliance_whitelist.json 路径")
    parser.add_argument("--licenses-json", help="可选：外部资产商用授权清单 licenses.json 路径")
    parser.add_argument("--hook-variants", type=int, default=1, help="可选：生成多套开篇钩子/封面变体对比方案 (如 2 或 3)")
    parser.add_argument("--dialect", choices=["mandarin", "cantonese", "dialect"], default="mandarin", help="口播语种，用于 #21 盲听代理")
    parser.add_argument("--store-anchor", help="门店听觉锚点（如门店名），用于 #21 盲听核验")
    parser.add_argument("--require-lock", action="store_true", help="正式渲染前强制校验 approval_lock.json 指纹锁")
    parser.add_argument("--strict-asr", action="store_true", help="无本地 Whisper 模型时阻止 #21 通过")
    parser.add_argument("--keep-temp", action="store_true", help="调试用：完工后保留大体积中间临时文件")
    parser.add_argument("--simulate-jy-fail", action="store_true", help="测试剪映草稿失效降级保护")
    parser.add_argument("--simulate-hf-fail", action="store_true", help="测试 HyperFrames 草稿失效降级保护")
    args = parser.parse_args()

    ws = resolve_workspace_paths(
        account=args.account,
        project_id=args.project_id,
        project_dir=Path(args.project_dir) if args.project_dir else None,
        raw_dir=Path(args.raw_dir) if args.raw_dir else None,
        workspace_root=Path(args.workspace_root) if args.workspace_root else None,
    )
    project_dir = ws["project_dir"]
    raw_dir = ws["raw_dir"]
    pipeline_dir = ws["pipeline_dir"]
    pipeline_dir.mkdir(parents=True, exist_ok=True)
    if ws["redirected_from_raw"]:
        print(f"[workspace] 检测到加工目录与原素材目录重叠，已强制隔离至专属工作区 -> {project_dir}")
    else:
        print(f"[workspace] 账号={ws['account_slug']} | 加工目录={project_dir} | 原素材只读源={raw_dir}")

    account_profile: Optional[Dict[str, Any]] = None
    prof_path = Path(args.account_profile) if args.account_profile else ws["account_profile_path"]
    if prof_path.exists():
        account_profile = read_json(prof_path)
        print(f"[pipeline] 已载入账号参数基因 ({ws['account_slug']}) -> {prof_path.name}")

    whitelist_path = Path(args.whitelist) if args.whitelist else ws["compliance_whitelist_path"]
    licenses_path = Path(args.licenses_json) if args.licenses_json else None
    visual_index_path = Path(args.visual_index) if args.visual_index else None

    raw_intel_obj: Optional[Dict[str, Any]] = None
    if args.raw_intel and Path(args.raw_intel).exists():
        dst_intel = pipeline_dir / "00_raw_intel.json"
        if Path(args.raw_intel).resolve() != dst_intel.resolve():
            shutil.copy2(args.raw_intel, dst_intel)
        raw_intel_obj = read_json(dst_intel)

    active_style_id = args.style_id or (account_profile.get("preferred_style_id") if account_profile else None)
    j_cut_frames = 3 if args.j_cut_frames is None else int(args.j_cut_frames)
    subtitle_template = args.subtitle_template
    lut_name = args.lut
    bgm_p = Path(args.bgm) if args.bgm else None

    if active_style_id:
        rt_cfg = resolve_style_runtime_config(active_style_id)
        if not subtitle_template:
            subtitle_template = rt_cfg["subtitle_template"]
        if not lut_name:
            lut_name = rt_cfg["lut_file"]
        if args.j_cut_frames is None:
            j_cut_frames = int(rt_cfg["j_cut_frames"])  # 严格保留 0 帧直切，禁止 `or 3` 吞掉
        write_json(pipeline_dir / "style_spectrum_applied.json", {
            "style_id": active_style_id,
            "style_name_cn": rt_cfg["style_name_cn"],
            "archetype_id": rt_cfg["archetype_id"],
        })
        if bgm_p is None:
            try:
                from vibe_sound import get_style_sound_profile
                v_prof = get_style_sound_profile(active_style_id)
                if v_prof and v_prof.get("bgm_wav") and Path(v_prof["bgm_wav"]).exists():
                    bgm_p = Path(v_prof["bgm_wav"])
            except Exception:
                pass
        print(
            f"[pipeline] 已挂载风格谱 {active_style_id} ({rt_cfg['style_name_cn']}) -> "
            f"字幕={subtitle_template}, LUT={lut_name}, J-cut={j_cut_frames}帧"
        )

    script_p = Path(args.script_json) if args.script_json else None
    if script_p and script_p.exists():
        _run_script_prelint(script_p, pipeline_dir, raw_intel=raw_intel_obj)
    bench_p = Path(args.benchmark) if args.benchmark else None

    def _ensure_effective_raw_dir() -> Path:
        has_media = raw_dir.is_dir() and any(
            p.is_file() and p.suffix.lower() in MEDIA_EXTS for p in raw_dir.iterdir()
        )
        if has_media:
            return raw_dir
        demo_dir = project_dir / "_demo_raw_media"
        if not (demo_dir.is_dir() and any(p.is_file() and p.suffix.lower() in MEDIA_EXTS for p in demo_dir.iterdir())):
            print("[pipeline] 未检测到原始素材，正在专属工作区内生成演示素材包（零写入原目录）...")
            generate_demo_media_pack(demo_dir, style_id=active_style_id)
        return demo_dir

    def _run_inventory_if_needed(src_raw: Path) -> None:
        raw_intel_p = pipeline_dir / "00_raw_intel.json"
        raw_intel = read_json(raw_intel_p) if raw_intel_p.is_file() else None
        contact_jpg = pipeline_dir / "00_contact_sheet.jpg"
        inv = inventory_directory(
            src_raw, args.track,
            raw_intel=raw_intel,
            contact_sheet_path=contact_jpg,
            visual_index_path=visual_index_path,
        )
        write_json(pipeline_dir / "00_inventory.json", inv)
        write_json(pipeline_dir / "00_shot_log.json", build_shot_log_dict(inv))
        (pipeline_dir / "00_inventory.md").write_text(render_markdown_summary(inv), encoding="utf-8")

    if args.mode == "render-locked":
        _rebuild_normalized_if_missing(pipeline_dir)
        res = _render_and_verify_only(
            pipeline_dir=pipeline_dir,
            track=args.track,
            benchmark=bench_p,
            simulate_jy_fail=args.simulate_jy_fail,
            simulate_hf_fail=args.simulate_hf_fail,
            dialect=args.dialect,
            store_anchor=args.store_anchor,
            require_lock=True if not args.require_lock else args.require_lock,
            strict_asr=args.strict_asr,
            hook_variants=args.hook_variants,
            ws=ws,
        )
    else:
        if args.mode in ("full", "lock-preview"):
            eff_raw = _ensure_effective_raw_dir()
            _run_inventory_if_needed(eff_raw)
            run_step1([eff_raw], pipeline_dir)
            run_step2(
                pipeline_dir,
                track=args.track,
                script_json_path=script_p,
                force_tts=args.force_tts,
                whitelist_path=whitelist_path,
            )
            run_step3(
                pipeline_dir,
                track=args.track,
                bgm_path=bgm_p,
                pause_sec=args.pause_sec,
                account_profile=account_profile,
            )
        elif args.mode == "relock-voice":
            if not (pipeline_dir / "01_manifest.json").exists():
                eff_raw = _ensure_effective_raw_dir()
                _run_inventory_if_needed(eff_raw)
                run_step1([eff_raw], pipeline_dir)
            else:
                _rebuild_normalized_if_missing(pipeline_dir)
            run_step2(
                pipeline_dir,
                track=args.track,
                script_json_path=script_p,
                force_tts=True if script_p else args.force_tts,
                whitelist_path=whitelist_path,
            )
            run_step3(
                pipeline_dir,
                track=args.track,
                bgm_path=bgm_p,
                pause_sec=args.pause_sec,
                account_profile=account_profile,
            )
        elif args.mode == "reedit-visuals":
            if not (pipeline_dir / "03_audio_events.json").exists():
                raise FileNotFoundError("reedit-visuals 模式要求 03_audio_events.json 已锁定存在。")
            _rebuild_normalized_if_missing(pipeline_dir)
            print("[pipeline] 只改画面模式：Step 1–3 已锁定音轨保持原封不动，仅按音轨事件重排画面。")

        res = run_visual_to_delivery(
            pipeline_dir=pipeline_dir,
            track=args.track,
            j_cut_frames=j_cut_frames,
            subtitle_template=subtitle_template,
            lut_name=lut_name,
            prefer_broll_only=args.prefer_broll_only,
            benchmark=bench_p,
            simulate_jy_fail=args.simulate_jy_fail,
            simulate_hf_fail=args.simulate_hf_fail,
            preview_only=(args.mode == "lock-preview"),
            style_id=active_style_id,
            account_profile=account_profile,
            dialect=args.dialect,
            store_anchor=args.store_anchor,
            require_lock=args.require_lock,
            strict_asr=args.strict_asr,
            licenses_path=licenses_path,
            hook_variants=args.hook_variants,
            ws=ws,
        )

    if args.mode != "lock-preview" and not res["qa_report"]["all_passed"]:
        print("[ERROR] 成片未通过 22 项自动与黑盒质检，已阻断交付！请查看 10_qa_report.md（中间文件已保留供排错）")
        sys.exit(2)

    if args.mode != "lock-preview":
        tidy_workspace(pipeline_dir, out_dir=ws["out_dir"], keep_temp=args.keep_temp)

    print("\n[OK] 流水线执行完毕。")


if __name__ == "__main__":
    main()
