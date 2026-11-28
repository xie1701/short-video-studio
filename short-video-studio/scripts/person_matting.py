#!/usr/bin/env python3
"""
[真·逐帧 AI 人像前景抠像与演播室背景引擎]
1. macOS 平台通过原生 Swift + Apple Vision (VNGeneratePersonSegmentationRequest) + CoreImage Metal GPU
   硬件加速逐帧生成 30fps 人像前景 Alpha 视频（matte.mp4）与沿头发/肩膀真实轮廓膨胀的纯白描边蒙版视频（outline.mp4）。
   首次运行自动编译并缓存至 ~/.short-video-studio/bin/svs_person_matte（约 2 秒），后续毫秒级启动。
2. 非 macOS 平台自动探测 mediapipe SelfieSegmentation；若均不可用则返回 None 供渲染器平滑降级。
3. 提供 style_19 专属冷峻数据新闻动态折线背景视频生成器（render_cool_data_news_bg_mp4）。
"""

from __future__ import annotations

import hashlib
import math
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, Optional

SCRIPTS_ROOT = Path(__file__).resolve().parent
if str(SCRIPTS_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_ROOT))

from common import (
    BT709_FFMPEG_ARGS,
    CANVAS_HEIGHT,
    CANVAS_WIDTH,
    DEFAULT_FPS,
    find_bin,
    find_commercial_font,
    get_cached_font,
    maybe_reexec_in_venv,
    run_cmd,
)

if __name__ == "__main__" or str(sys.argv[0]).endswith(".py"):
    maybe_reexec_in_venv()

import numpy as np
from PIL import Image, ImageDraw

_SWIFT_MATTE_SOURCE = r'''
import Foundation
import AVFoundation
import Vision
import CoreImage
import CoreVideo

func die(_ msg: String, code: Int32 = 1) -> Never {
    FileHandle.standardError.write(Data((msg + "\n").utf8))
    exit(code)
}

let args = CommandLine.arguments
if args.count < 3 {
    die("Usage: svs_person_matte <in.mp4> <out_matte.mp4> [out_outline.mp4] [outline_px] [quality:balanced|accurate] [edge:smooth|torn]")
}

let inURL = URL(fileURLWithPath: args[1])
let outMatteURL = URL(fileURLWithPath: args[2])
let outOutlinePath = args.count >= 4 ? args[3] : ""
let outlinePx = args.count >= 5 ? max(0.0, Double(args[4]) ?? 0.0) : 0.0
let qualityArg = args.count >= 6 ? args[5] : "accurate"
let edgeModeArg = args.count >= 7 ? args[6] : "smooth"

let asset = AVURLAsset(url: inURL)
let sem = DispatchSemaphore(value: 0)
var videoTrack: AVAssetTrack? = nil
if #available(macOS 12.0, *) {
    Task {
        let tracks = try? await asset.loadTracks(withMediaType: .video)
        videoTrack = tracks?.first
        sem.signal()
    }
    sem.wait()
} else {
    videoTrack = asset.tracks(withMediaType: .video).first
}

guard let track = videoTrack else {
    die("No video track found in input")
}

let naturalSize = track.naturalSize
let prefTransform = track.preferredTransform
let transformedSize = naturalSize.applying(prefTransform)
let width = max(2, Int(abs(transformedSize.width).rounded()) & ~1)
let height = max(2, Int(abs(transformedSize.height).rounded()) & ~1)

// 前导「空 edit（elst）」会让 AVAssetReader 在轨道起点**合成一帧全黑样本**（PTS=0）。
// 实测证据（修复前的抽段 mp4）：track.segments = [empty [0,0.033), real [0.033,1.733)]，
// 读到的第 0 个 sample PTS=0、BGRA 均值 0.0 / max=2，第 1 个 sample 才是真画面；
// 而修复后的抽段（setpts=PTS-STARTPTS 从源头归零）segments = [real [0,1.7)]，无补黑帧。
// Vision 对全黑帧返回空 mask → matte 前 1–2 帧全 0、撕纸人像层整层缺失，成片 0.0s 的差就从这来。
// 抽段端已经修了；这里按空 edit 时长把补黑样帧丢掉，作为第二道防线（旧缓存 / 外部素材仍可能带 elst）。
var emptyLeadSec = 0.0
for seg in track.segments {
    if seg.isEmpty {
        emptyLeadSec += CMTimeGetSeconds(seg.timeMapping.target.duration)
    } else {
        break
    }
}

guard let reader = try? AVAssetReader(asset: asset) else {
    die("Failed to create AVAssetReader")
}
let readerSettings: [String: Any] = [
    kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA)
]
let readerOutput = AVAssetReaderTrackOutput(track: track, outputSettings: readerSettings)
readerOutput.alwaysCopiesSampleData = false
reader.add(readerOutput)

func makeWriter(url: URL, w: Int, h: Int) -> (AVAssetWriter, AVAssetWriterInput, AVAssetWriterInputPixelBufferAdaptor) {
    try? FileManager.default.removeItem(at: url)
    guard let writer = try? AVAssetWriter(outputURL: url, fileType: .mp4) else {
        die("Failed to create AVAssetWriter for \(url.path)")
    }
    let videoSettings: [String: Any] = [
        AVVideoCodecKey: AVVideoCodecType.h264,
        AVVideoWidthKey: w,
        AVVideoHeightKey: h,
        AVVideoCompressionPropertiesKey: [
            AVVideoAverageBitRateKey: 6_000_000,
            AVVideoProfileLevelKey: AVVideoProfileLevelH264HighAutoLevel,
        ],
    ]
    let input = AVAssetWriterInput(mediaType: .video, outputSettings: videoSettings)
    input.expectsMediaDataInRealTime = false
    let pbAttrs: [String: Any] = [
        kCVPixelBufferPixelFormatTypeKey as String: Int(kCVPixelFormatType_32BGRA),
        kCVPixelBufferWidthKey as String: w,
        kCVPixelBufferHeightKey as String: h,
    ]
    let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: input, sourcePixelBufferAttributes: pbAttrs)
    writer.add(input)
    return (writer, input, adaptor)
}

let (matteWriter, matteInput, matteAdaptor) = makeWriter(url: outMatteURL, w: width, h: height)
var outlineTuple: (AVAssetWriter, AVAssetWriterInput, AVAssetWriterInputPixelBufferAdaptor)? = nil
if !outOutlinePath.isEmpty && outlinePx > 0.5 {
    outlineTuple = makeWriter(url: URL(fileURLWithPath: outOutlinePath), w: width, h: height)
}

guard reader.startReading() else {
    die("AVAssetReader failed to start: \(reader.error?.localizedDescription ?? "unknown")")
}
matteWriter.startWriting()
matteWriter.startSession(atSourceTime: .zero)
if let ot = outlineTuple {
    ot.0.startWriting()
    ot.0.startSession(atSourceTime: .zero)
}

let ciContext = CIContext(options: [.useSoftwareRenderer: false])
let seqHandler = VNSequenceRequestHandler()
let segReq = VNGeneratePersonSegmentationRequest()
segReq.qualityLevel = (qualityArg == "accurate") ? .accurate : .balanced
segReq.outputPixelFormat = kCVPixelFormatType_OneComponent8

let targetRect = CGRect(x: 0, y: 0, width: width, height: height)
let colorSpace = CGColorSpaceCreateDeviceRGB()

var frameCount = 0
var fgCoverageSum: Double = 0.0
var coverageSamples = 0
var firstPTS: CMTime? = nil

func makeContrastMask(_ ci: CIImage) -> CIImage {
    // 柔化上采样轮廓并保留平滑 3-4px 抗锯齿渐变边缘（彻底消除硬二值锯齿）
    var cur = ci
    if let blur = CIFilter(name: "CIGaussianBlur") {
        blur.setValue(cur, forKey: kCIInputImageKey)
        blur.setValue(1.3, forKey: kCIInputRadiusKey)
        if let out = blur.outputImage {
            cur = out.cropped(to: targetRect)
        }
    }
    if let f = CIFilter(name: "CIColorControls") {
        f.setValue(cur, forKey: kCIInputImageKey)
        f.setValue(1.25, forKey: kCIInputContrastKey)
        f.setValue(0.0, forKey: kCIInputBrightnessKey)
        if let out = f.outputImage {
            return out.cropped(to: targetRect)
        }
    }
    return cur
}

func makeDilatedOutline(_ ci: CIImage, radius: Double, torn: Bool = false) -> CIImage {
    // 沿人像平滑膨胀后做高斯抗锯齿羽化，彻底禁用噪声撕纸置换与高对比度硬切边
    var cur = ci
    if let preBlur = CIFilter(name: "CIGaussianBlur") {
        preBlur.setValue(cur, forKey: kCIInputImageKey)
        preBlur.setValue(max(1.2, radius * 0.20), forKey: kCIInputRadiusKey)
        if let out = preBlur.outputImage {
            cur = out.cropped(to: targetRect)
        }
    }
    if let morph = CIFilter(name: "CIMorphologyMaximum") {
        morph.setValue(cur, forKey: kCIInputImageKey)
        morph.setValue(max(1.0, radius * 0.85), forKey: kCIInputRadiusKey)
        if let out = morph.outputImage {
            cur = out.cropped(to: targetRect)
        }
    }
    if let postBlur = CIFilter(name: "CIGaussianBlur") {
        postBlur.setValue(cur, forKey: kCIInputImageKey)
        postBlur.setValue(1.6, forKey: kCIInputRadiusKey)
        if let out = postBlur.outputImage {
            cur = out.cropped(to: targetRect)
        }
    }
    if let ctrl = CIFilter(name: "CIColorControls") {
        ctrl.setValue(cur, forKey: kCIInputImageKey)
        ctrl.setValue(1.35, forKey: kCIInputContrastKey)
        ctrl.setValue(0.04, forKey: kCIInputBrightnessKey)
        if let out = ctrl.outputImage {
            cur = out.cropped(to: targetRect)
        }
    }
    return cur
}

func renderCI(_ ci: CIImage, into adaptor: AVAssetWriterInputPixelBufferAdaptor, pts: CMTime) {
    guard let pool = adaptor.pixelBufferPool else { return }
    var pbOut: CVPixelBuffer? = nil
    CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pbOut)
    guard let pb = pbOut else { return }
    ciContext.render(ci, to: pb, bounds: targetRect, colorSpace: colorSpace)
    while !adaptor.assetWriterInput.isReadyForMoreMediaData {
        usleep(1000)
    }
    adaptor.append(pb, withPresentationTime: pts)
}

while reader.status == .reading {
    guard let sampleBuf = readerOutput.copyNextSampleBuffer() else { break }
    let pts = CMSampleBufferGetPresentationTimeStamp(sampleBuf)
    // 丢掉前导空 edit 合成的补黑样帧（见上：其 PTS 落在 [0, emptyLeadSec)）。
    if emptyLeadSec > 1e-6 && CMTimeGetSeconds(pts) + 1e-6 < emptyLeadSec { continue }
    // 输入首帧 PTS 常不为 0（剪映源素材 start_time=0.033，ffmpeg 抽段会把它带下来）。
    // 若直接按原 PTS 喂给 startSession(.zero) 的 writer，AVAssetWriter 会拿**黑帧**去填
    // [0, firstPTS) 这段空洞，帧率越高填得越多：60fps 输入补 2 帧、30fps 补 1 帧实测。
    // 那些黑帧在图层面板上就是「前两帧没有抠像」，本机实测 matte 0/1 帧全 0（后续帧 34% 覆盖）。
    // 所以这里统一把 PTS 归零到首帧，输出帧数与输入一一对应。
    if firstPTS == nil { firstPTS = pts }
    let relPTS = CMTimeSubtract(pts, firstPTS!)
    guard let imgBuf = CMSampleBufferGetImageBuffer(sampleBuf) else { continue }

    var maskCI: CIImage
    do {
        try seqHandler.perform([segReq], on: imgBuf)
        if let obs = segReq.results?.first as? VNPixelBufferObservation {
            let mBuf = obs.pixelBuffer
            if frameCount % 10 == 0 {
                CVPixelBufferLockBaseAddress(mBuf, .readOnly)
                let mw = CVPixelBufferGetWidth(mBuf)
                let mh = CVPixelBufferGetHeight(mBuf)
                let bpr = CVPixelBufferGetBytesPerRow(mBuf)
                if let base = CVPixelBufferGetBaseAddress(mBuf) {
                    let ptr = base.assumingMemoryBound(to: UInt8.self)
                    var fgCnt = 0
                    var totCnt = 0
                    let stepY = max(1, mh / 32)
                    let stepX = max(1, mw / 32)
                    var y = 0
                    while y < mh {
                        let row = ptr + y * bpr
                        var x = 0
                        while x < mw {
                            if row[x] >= 120 { fgCnt += 1 }
                            totCnt += 1
                            x += stepX
                        }
                        y += stepY
                    }
                    if totCnt > 0 {
                        fgCoverageSum += Double(fgCnt) / Double(totCnt)
                        coverageSamples += 1
                    }
                }
                CVPixelBufferUnlockBaseAddress(mBuf, .readOnly)
            }
            let rawMask = CIImage(cvPixelBuffer: mBuf)
            let sx = CGFloat(width) / max(1.0, rawMask.extent.width)
            let sy = CGFloat(height) / max(1.0, rawMask.extent.height)
            let scaled = rawMask.transformed(by: CGAffineTransform(scaleX: sx, y: sy)).cropped(to: targetRect)
            maskCI = makeContrastMask(scaled)
        } else {
            maskCI = CIImage(color: CIColor.black).cropped(to: targetRect)
        }
    } catch {
        maskCI = CIImage(color: CIColor.black).cropped(to: targetRect)
    }

    renderCI(maskCI, into: matteAdaptor, pts: relPTS)
    if let ot = outlineTuple {
        let dilCI = makeDilatedOutline(maskCI, radius: outlinePx, torn: edgeModeArg == "torn")
        renderCI(dilCI, into: ot.2, pts: relPTS)
    }
    frameCount += 1
}

matteInput.markAsFinished()
let finSem = DispatchSemaphore(value: 0)
matteWriter.finishWriting { finSem.signal() }
finSem.wait()

if let ot = outlineTuple {
    ot.1.markAsFinished()
    let finSem2 = DispatchSemaphore(value: 0)
    ot.0.finishWriting { finSem2.signal() }
    finSem2.wait()
}

let avgCov = coverageSamples > 0 ? (fgCoverageSum / Double(coverageSamples)) : 0.0
let jsonOut = String(format: "{\"frames\":%d,\"width\":%d,\"height\":%d,\"person_coverage\":%.4f,\"has_person\":%@}",
                     frameCount, width, height, avgCov, avgCov >= 0.02 ? "true" : "false")
print(jsonOut)
'''


def _matte_bin_path() -> Path:
    base = Path.home() / ".short-video-studio" / "bin"
    base.mkdir(parents=True, exist_ok=True)
    return base / "svs_person_matte"


def ensure_macos_vision_matte_bin() -> Optional[Path]:
    """在 macOS 上按源码指纹编译并缓存原生 Vision+Metal 人像抠像二进制。"""
    if platform.system() != "Darwin":
        return None
    swiftc = shutil.which("swiftc")
    if not swiftc:
        return None
    bin_p = _matte_bin_path()
    hash_p = bin_p.with_suffix(".sha256")
    src_hash = hashlib.sha256(_SWIFT_MATTE_SOURCE.encode("utf-8")).hexdigest()[:16]
    if bin_p.is_file() and hash_p.is_file() and hash_p.read_text(encoding="utf-8").strip() == src_hash:
        return bin_p

    src_p = bin_p.with_suffix(".swift")
    src_p.write_text(_SWIFT_MATTE_SOURCE, encoding="utf-8")
    tmp_bin = bin_p.with_suffix(".tmp")
    cmd = [
        swiftc,
        "-O",
        "-framework", "AVFoundation",
        "-framework", "Vision",
        "-framework", "CoreImage",
        "-framework", "CoreVideo",
        str(src_p),
        "-o", str(tmp_bin),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0 or not tmp_bin.is_file():
        tmp_bin.unlink(missing_ok=True)
        return None
    tmp_bin.replace(bin_p)
    try:
        os.chmod(bin_p, 0o755)
    except Exception:
        pass
    hash_p.write_text(src_hash, encoding="utf-8")
    return bin_p


def _generate_mediapipe_matte_video(
    src_mp4: Path,
    out_matte_mp4: Path,
    out_outline_mp4: Optional[Path] = None,
    outline_px: int = 10,
    fps: int = DEFAULT_FPS,
    torn_paper: bool = False,
) -> Optional[Dict[str, Any]]:
    """非 macOS 跨平台兜底：检测 mediapipe + cv2 逐帧生成人像 Alpha 与轮廓白边。"""
    try:
        import cv2  # type: ignore
        import mediapipe as mp  # type: ignore
    except Exception:
        return None

    ffmpeg_bin = find_bin("ffmpeg")
    if not ffmpeg_bin:
        return None

    cap = cv2.VideoCapture(str(src_mp4))
    if not cap.isOpened():
        return None
    w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or CANVAS_WIDTH
    h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or CANVAS_HEIGHT
    src_fps = cap.get(cv2.CAP_PROP_FPS) or float(fps)

    out_matte_mp4.parent.mkdir(parents=True, exist_ok=True)
    m_proc = subprocess.Popen(
        [
            ffmpeg_bin, "-y", "-f", "rawvideo", "-pix_fmt", "gray",
            "-s", f"{w}x{h}", "-r", f"{src_fps:.3f}", "-i", "pipe:0",
            "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
            str(out_matte_mp4),
        ],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    o_proc = None
    if out_outline_mp4 and outline_px > 0:
        out_outline_mp4.parent.mkdir(parents=True, exist_ok=True)
        o_proc = subprocess.Popen(
            [
                ffmpeg_bin, "-y", "-f", "rawvideo", "-pix_fmt", "gray",
                "-s", f"{w}x{h}", "-r", f"{src_fps:.3f}", "-i", "pipe:0",
                "-c:v", "libx264", "-preset", "fast", "-crf", "16", "-pix_fmt", "yuv420p",
                str(out_outline_mp4),
            ],
            stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (outline_px * 2 + 1, outline_px * 2 + 1)) if outline_px > 0 else None
    frames = 0
    cov_sum = 0.0
    try:
        with mp.solutions.selfie_segmentation.SelfieSegmentation(model_selection=1) as seg:
            while True:
                ok, bgr = cap.read()
                if not ok:
                    break
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                res = seg.process(rgb)
                if res.segmentation_mask is not None:
                    mask_f = np.clip((res.segmentation_mask - 0.35) / 0.35, 0.0, 1.0)
                    mask_u8 = (mask_f * 255.0).astype(np.uint8)
                else:
                    mask_u8 = np.zeros((h, w), dtype=np.uint8)
                if frames % 10 == 0:
                    cov_sum += float(np.mean(mask_u8 >= 120))
                if m_proc.stdin:
                    m_proc.stdin.write(mask_u8.tobytes())
                if o_proc and o_proc.stdin and kernel is not None:
                    dil = cv2.dilate((mask_u8 >= 120).astype(np.uint8) * 255, kernel, iterations=1)
                    dil = cv2.GaussianBlur(dil, (0, 0), sigmaX=1.8)
                    o_proc.stdin.write(dil.tobytes())
                frames += 1
    finally:
        cap.release()
        if m_proc.stdin:
            m_proc.stdin.close()
        m_proc.wait()
        if o_proc:
            if o_proc.stdin:
                o_proc.stdin.close()
            o_proc.wait()

    avg_cov = cov_sum / max(1, (frames + 9) // 10)
    return {
        "backend": "mediapipe",
        "frames": frames,
        "width": w,
        "height": h,
        "person_coverage": round(avg_cov, 4),
        "has_person": avg_cov >= 0.02,
        "matte_mp4": str(out_matte_mp4),
        "outline_mp4": str(out_outline_mp4) if (out_outline_mp4 and out_outline_mp4.is_file()) else None,
    }


def generate_person_matte_video(
    src_mp4: Path,
    out_matte_mp4: Path,
    out_outline_mp4: Optional[Path] = None,
    outline_px: int = 10,
    quality: str = "accurate",
    fps: int = DEFAULT_FPS,
    torn_paper: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    为指定视频逐帧生成人像前景 Alpha 蒙版视频（及可选沿人体轮廓膨胀的白边蒙版视频）。
    先写同目录临时文件、成功且确认有人像后再原子替换目标文件，防止半成品被误当缓存复用。
    """
    import json
    import os

    src_mp4 = Path(src_mp4)
    out_matte_mp4 = Path(out_matte_mp4)
    if not src_mp4.is_file():
        return None
    out_matte_mp4.parent.mkdir(parents=True, exist_ok=True)
    tmp_matte = out_matte_mp4.with_name(f".{out_matte_mp4.stem}.tmp.{os.getpid()}.mp4")
    tmp_outline = (
        Path(out_outline_mp4).with_name(f".{Path(out_outline_mp4).stem}.tmp.{os.getpid()}.mp4")
        if (out_outline_mp4 and outline_px > 0)
        else None
    )

    def _finalize(meta: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
        if not meta or not meta.get("has_person") or not tmp_matte.is_file() or tmp_matte.stat().st_size == 0:
            tmp_matte.unlink(missing_ok=True)
            if tmp_outline:
                tmp_outline.unlink(missing_ok=True)
            # 清理可能残留的旧半成品
            out_matte_mp4.unlink(missing_ok=True)
            if out_outline_mp4:
                Path(out_outline_mp4).unlink(missing_ok=True)
            return meta if (meta and not meta.get("has_person")) else None
        tmp_matte.replace(out_matte_mp4)
        meta["matte_mp4"] = str(out_matte_mp4)
        if tmp_outline and out_outline_mp4 and tmp_outline.is_file() and tmp_outline.stat().st_size > 0:
            tmp_outline.replace(out_outline_mp4)
            meta["outline_mp4"] = str(out_outline_mp4)
        else:
            if tmp_outline:
                tmp_outline.unlink(missing_ok=True)
            meta["outline_mp4"] = None
        return meta

    # 1. macOS 原生 Swift + Apple Vision + Metal GPU 硬件加速（首选）
    bin_p = ensure_macos_vision_matte_bin()
    if bin_p is not None:
        args = [
            str(bin_p),
            str(src_mp4),
            str(tmp_matte),
            str(tmp_outline) if tmp_outline else "",
            str(max(0, int(outline_px))),
            quality if quality in ("balanced", "accurate") else "accurate",
            "smooth",
        ]
        try:
            proc = subprocess.run(args, capture_output=True, text=True, timeout=180)
            if proc.returncode == 0 and tmp_matte.is_file():
                meta_line = (proc.stdout or "").strip().splitlines()[-1] if (proc.stdout or "").strip() else "{}"
                meta = json.loads(meta_line)
                meta["backend"] = "macos_vision"
                return _finalize(meta)
        except Exception:
            pass
        tmp_matte.unlink(missing_ok=True)
        if tmp_outline:
            tmp_outline.unlink(missing_ok=True)

    # 2. 非 macOS 探测 mediapipe
    mp_meta = _generate_mediapipe_matte_video(
        src_mp4=src_mp4,
        out_matte_mp4=tmp_matte,
        out_outline_mp4=tmp_outline,
        outline_px=outline_px,
        fps=fps,
        torn_paper=torn_paper,
    )
    return _finalize(mp_meta)


def render_cool_data_news_bg_mp4(
    out_mp4: Path,
    duration_sec: float,
    fps: int = DEFAULT_FPS,
    width: int = CANVAS_WIDTH,
    height: int = CANVAS_HEIGHT,
) -> Path:
    """
    为 style_19（冷峻数据新闻前景）程序化生成 30fps 深青蓝金融数据折线与动态网格演播室背景视频。
    """
    out_mp4 = Path(out_mp4)
    out_mp4.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg_bin = find_bin("ffmpeg")
    if not ffmpeg_bin:
        raise RuntimeError("未找到 ffmpeg，无法生成数据新闻背景。")

    n_frames = max(15, int(round(max(0.5, duration_sec) * fps)))
    # 预先生成静态深青蓝渐变 + 细密坐标网格底图（高效复用）
    yy = np.linspace(0.0, 1.0, height, dtype=np.float32)[:, None]
    xx = np.linspace(0.0, 1.0, width, dtype=np.float32)[None, :]
    r_ch = (8.0 + 10.0 * (1.0 - yy) + 14.0 * np.exp(-((xx - 0.5) ** 2 / 0.18 + (yy - 0.35) ** 2 / 0.14))).clip(0, 255)
    g_ch = (20.0 + 26.0 * (1.0 - yy) + 42.0 * np.exp(-((xx - 0.5) ** 2 / 0.22 + (yy - 0.35) ** 2 / 0.16))).clip(0, 255)
    b_ch = (34.0 + 44.0 * (1.0 - yy) + 64.0 * np.exp(-((xx - 0.5) ** 2 / 0.24 + (yy - 0.35) ** 2 / 0.18))).clip(0, 255)
    base_rgb = np.stack([r_ch, g_ch, b_ch], axis=-1).astype(np.uint8)
    base_img = Image.fromarray(base_rgb, mode="RGB")
    bd = ImageDraw.Draw(base_img)
    for gx in range(0, width, 72):
        bd.line([(gx, 0), (gx, height)], fill=(28, 62, 86), width=1)
    for gy in range(0, height, 72):
        bd.line([(0, gy), (width, gy)], fill=(28, 62, 86), width=1)
    # 顶部新闻频道细装饰条与刻度
    bd.rectangle([(0, 148), (width, 152)], fill=(56, 189, 248))
    bd.rectangle([(0, 1088), (width, 1091)], fill=(30, 82, 114))
    mono_fnt = get_cached_font(find_commercial_font(["AlimamaShuHeiTi-Bold.otf"]), 22)
    bd.text((64, 106), "DATA BRIEFING  //  REALTIME INDEX", font=mono_fnt, fill=(125, 211, 252))
    bd.text((width - 260, 106), "LIVE  30FPS", font=mono_fnt, fill=(56, 189, 248))

    cmd = [
        ffmpeg_bin, "-y",
        "-f", "rawvideo", "-vcodec", "rawvideo",
        "-pix_fmt", "rgb24", "-s", f"{width}x{height}", "-r", str(fps),
        "-i", "pipe:0",
        "-an", "-c:v", "libx264", "-preset", "fast", "-crf", "17",
        "-pix_fmt", "yuv420p", *BT709_FFMPEG_ARGS,
        str(out_mp4),
    ]
    proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        # K 线柱 + 双条流动折线 + 呼吸扫描光
        xs = list(range(60, width - 50, 36))
        for fi in range(n_frames):
            t = fi / float(fps)
            frame = base_img.copy()
            d = ImageDraw.Draw(frame, "RGBA")

            # 缓慢平移的数据柱状底纹
            for bi, bx in enumerate(xs):
                bh = int(70 + 110 * (0.5 + 0.5 * math.sin(bi * 0.55 + t * 1.4)))
                d.rectangle([(bx - 8, 1070 - bh), (bx + 8, 1070)], fill=(56, 189, 248, 42))

            # 主折线（冷青亮线）
            pts_main = []
            pts_sub = []
            for bi, bx in enumerate(xs):
                vy = int(560 - 135 * math.sin(bi * 0.35 + t * 1.1) - 55 * math.cos(bi * 0.72 - t * 0.7))
                vy2 = int(680 - 95 * math.cos(bi * 0.28 + t * 0.85) - 35 * math.sin(bi * 0.65 + t * 1.3))
                pts_main.append((bx, vy))
                pts_sub.append((bx, vy2))

            if len(pts_main) >= 2:
                d.line(pts_main, fill=(56, 189, 248, 210), width=4)
                d.line(pts_sub, fill=(148, 163, 184, 130), width=2)
            # 脉冲高亮点
            hi_idx = int((t * 4.5) % len(pts_main))
            hx, hy = pts_main[hi_idx]
            d.ellipse([(hx - 14, hy - 14), (hx + 14, hy + 14)], fill=(56, 189, 248, 80))
            d.ellipse([(hx - 6, hy - 6), (hx + 6, hy + 6)], fill=(224, 242, 254, 245))

            # 缓慢纵向扫描光线
            scan_y = int(180 + ((t * 140.0) % 900))
            d.rectangle([(0, scan_y), (width, scan_y + 4)], fill=(125, 211, 252, 46))

            if proc.stdin:
                proc.stdin.write(frame.tobytes())
    finally:
        if proc.stdin:
            proc.stdin.close()
        proc.wait()
    if proc.returncode != 0 or not out_mp4.is_file():
        raise RuntimeError(f"冷峻数据背景渲染失败: {out_mp4}")
    return out_mp4


if __name__ == "__main__":
    bp = ensure_macos_vision_matte_bin()
    print(f"[person_matting] macOS Vision binary: {bp}")
