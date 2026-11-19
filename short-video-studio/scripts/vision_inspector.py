#!/usr/bin/env python3
"""
[跨平台视觉原材感知引擎]
- macOS 优先编译并缓存原生 Apple Vision Swift CLI (~/.short-video-studio/cache/svs_vision_inspector)：
  单次执行并行跑通人脸/五官关键点 (VNDetectFaceLandmarksRequest)、主体显著性热区
  (VNGenerateAttentionBasedSaliencyImageRequest) 与精确 OCR (VNRecognizeTextRequest)。
- 非 macOS 或无 swiftc 环境优雅降级为 PIL + numpy/scipy 亮度梯度显著性估算。
- 提供纯 numpy/scipy 拉普拉斯锐度方差与包围盒 IoU 工具函数。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from common import maybe_reexec_in_venv

maybe_reexec_in_venv()

import numpy as np
from PIL import Image

VISION_INSPECTOR_SWIFT_SRC = r"""
import Foundation
import Vision
import ImageIO

func topLeftRect(_ b: CGRect) -> [String: Double] {
    return [
        "x": Double(b.origin.x),
        "y": Double(1.0 - b.origin.y - b.size.height),
        "w": Double(b.size.width),
        "h": Double(b.size.height)
    ]
}

func eyeMetrics(_ region: VNFaceLandmarkRegion2D?, faceBox b: CGRect) -> (cx: Double, cy: Double, openRatio: Double)? {
    guard let pts = region?.normalizedPoints, pts.count >= 2 else { return nil }
    var sumX = 0.0
    var sumY = 0.0
    var minX = 1.0, maxX = 0.0
    var minY = 1.0, maxY = 0.0
    for pt in pts {
        let gx = Double(b.origin.x) + Double(pt.x) * Double(b.size.width)
        let gy = 1.0 - (Double(b.origin.y) + Double(pt.y) * Double(b.size.height))
        sumX += gx
        sumY += gy
        minX = min(minX, Double(pt.x))
        maxX = max(maxX, Double(pt.x))
        minY = min(minY, Double(pt.y))
        maxY = max(maxY, Double(pt.y))
    }
    let w = max(1e-5, maxX - minX)
    let h = max(0.0, maxY - minY)
    let cnt = Double(pts.count)
    return (sumX / cnt, sumY / cnt, h / w)
}

func mouthOpenRatio(_ region: VNFaceLandmarkRegion2D?) -> Double {
    guard let pts = region?.normalizedPoints, pts.count >= 4 else { return 0.0 }
    var minX = 1.0, maxX = 0.0
    var minY = 1.0, maxY = 0.0
    for pt in pts {
        minX = min(minX, Double(pt.x))
        maxX = max(maxX, Double(pt.x))
        minY = min(minY, Double(pt.y))
        maxY = max(maxY, Double(pt.y))
    }
    let w = max(1e-5, maxX - minX)
    let h = max(0.0, maxY - minY)
    return h / w
}

let args = CommandLine.arguments
if args.count < 2 { exit(1) }

for path in args.dropFirst() {
    let url = URL(fileURLWithPath: path)
    guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
          let cgImg = CGImageSourceCreateImageAtIndex(src, 0, nil) else {
        let empty: [String: Any] = ["file": path, "width": 0, "height": 0, "faces": [], "saliency": NSNull(), "texts": []]
        if let d = try? JSONSerialization.data(withJSONObject: empty),
           let s = String(data: d, encoding: .utf8) { print(s) }
        continue
    }

    var cgOrientation = CGImagePropertyOrientation.up
    if let props = CGImageSourceCopyPropertiesAtIndex(src, 0, nil) as? [CFString: Any],
       let rawOri = props[kCGImagePropertyOrientation] as? UInt32,
       let parsedOri = CGImagePropertyOrientation(rawValue: rawOri) {
        cgOrientation = parsedOri
    }
    let swapped = (cgOrientation == .left || cgOrientation == .right || cgOrientation == .leftMirrored || cgOrientation == .rightMirrored)
    let dispWidth = swapped ? cgImg.height : cgImg.width
    let dispHeight = swapped ? cgImg.width : cgImg.height

    let faceReq = VNDetectFaceLandmarksRequest()
    let ocrReq = VNRecognizeTextRequest()
    ocrReq.recognitionLevel = .accurate
    ocrReq.recognitionLanguages = ["zh-Hans", "en-US"]
    ocrReq.usesLanguageCorrection = true

    var requests: [VNRequest] = [faceReq, ocrReq]
    var salReq: VNGenerateAttentionBasedSaliencyImageRequest? = nil
    if #available(macOS 10.15, *) {
        let sr = VNGenerateAttentionBasedSaliencyImageRequest()
        salReq = sr
        requests.append(sr)
    }

    let handler = VNImageRequestHandler(cgImage: cgImg, orientation: cgOrientation, options: [:])
    try? handler.perform(requests)

    var facesOut: [[String: Any]] = []
    for obs in faceReq.results ?? [] {
        let b = obs.boundingBox
        var faceDict: [String: Any] = ["bbox": topLeftRect(b)]
        let lEye = eyeMetrics(obs.landmarks?.leftEye, faceBox: b)
        let rEye = eyeMetrics(obs.landmarks?.rightEye, faceBox: b)
        if let le = lEye, let re = rEye {
            faceDict["eye_cx"] = (le.cx + re.cx) * 0.5
            faceDict["eye_cy"] = (le.cy + re.cy) * 0.5
            faceDict["eye_open_ratio"] = (le.openRatio + re.openRatio) * 0.5
        } else if let e = lEye ?? rEye {
            faceDict["eye_cx"] = e.cx
            faceDict["eye_cy"] = e.cy
            faceDict["eye_open_ratio"] = e.openRatio
        } else {
            faceDict["eye_cx"] = Double(b.origin.x + b.size.width * 0.5)
            faceDict["eye_cy"] = 1.0 - Double(b.origin.y + b.size.height * 0.62)
            faceDict["eye_open_ratio"] = 0.25
        }
        let mOpen = mouthOpenRatio(obs.landmarks?.innerLips ?? obs.landmarks?.outerLips)
        faceDict["mouth_open_ratio"] = mOpen
        facesOut.append(faceDict)
    }

    var salOut: Any = NSNull()
    if #available(macOS 10.15, *), let sr = salReq,
       let salObs = sr.results?.first as? VNSaliencyImageObservation,
       let objs = salObs.salientObjects, !objs.isEmpty {
        var uRect = objs[0].boundingBox
        for obj in objs.dropFirst() {
            uRect = uRect.union(obj.boundingBox)
        }
        salOut = topLeftRect(uRect)
    }

    var textsOut: [[String: Any]] = []
    for obs in ocrReq.results ?? [] {
        if let cand = obs.topCandidates(1).first {
            var item: [String: Any] = topLeftRect(obs.boundingBox)
            item["text"] = cand.string
            item["conf"] = Double(cand.confidence)
            textsOut.append(item)
        }
    }

    let out: [String: Any] = [
        "file": path,
        "width": dispWidth,
        "height": dispHeight,
        "faces": facesOut,
        "saliency": salOut,
        "texts": textsOut
    ]
    if let data = try? JSONSerialization.data(withJSONObject: out),
       let s = String(data: data, encoding: .utf8) {
        print(s)
    }
}
"""


def _src_hash() -> str:
    return hashlib.sha256(VISION_INSPECTOR_SWIFT_SRC.encode("utf-8")).hexdigest()[:12]


def ensure_vision_inspector_bin() -> Optional[Path]:
    """在 macOS 上编译并缓存原生 Apple Vision CLI；源文件变化时自动重编。"""
    if platform.system() != "Darwin":
        return None
    swiftc = shutil.which("swiftc")
    if not swiftc:
        print("[vision_inspector] 未检测到 swiftc，视觉感知将回退为降级模式。", file=sys.stderr)
        return None

    cache_dir = Path.home() / ".short-video-studio" / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    tag = _src_hash()
    bin_path = cache_dir / f"svs_vision_inspector_{tag}"
    src_path = cache_dir / f"svs_vision_inspector_{tag}.swift"
    if bin_path.exists():
        return bin_path

    src_path.write_text(VISION_INSPECTOR_SWIFT_SRC.strip() + "\n", encoding="utf-8")
    res = subprocess.run(
        [swiftc, "-O", str(src_path), "-o", str(bin_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if res.returncode == 0 and bin_path.exists():
        return bin_path
    print(f"[vision_inspector] swiftc 编译失败: {(res.stderr or '')[-400:]}", file=sys.stderr)
    return None


def compute_laplacian_sharpness(img_arr: np.ndarray) -> float:
    """计算图像灰度拉普拉斯算子方差，数值越高代表画面越锐利不虚焦。"""
    if img_arr.ndim == 3:
        gray = (
            0.299 * img_arr[:, :, 0].astype(np.float32)
            + 0.587 * img_arr[:, :, 1].astype(np.float32)
            + 0.114 * img_arr[:, :, 2].astype(np.float32)
        )
    else:
        gray = img_arr.astype(np.float32)
    if gray.shape[0] < 4 or gray.shape[1] < 4:
        return 0.0
    lap = (
        gray[:-2, 1:-1]
        + gray[2:, 1:-1]
        + gray[1:-1, :-2]
        + gray[1:-1, 2:]
        - 4.0 * gray[1:-1, 1:-1]
    )
    return round(float(np.var(lap)), 2)


def check_bbox_iou(b1: Dict[str, float], b2: Dict[str, float]) -> float:
    """计算两个 {x, y, w, h} 矩形包围盒的交并比 (IoU)。"""
    x1 = max(float(b1["x"]), float(b2["x"]))
    y1 = max(float(b1["y"]), float(b2["y"]))
    x2 = min(float(b1["x"]) + float(b1["w"]), float(b2["x"]) + float(b2["w"]))
    y2 = min(float(b1["y"]) + float(b1["h"]), float(b2["y"]) + float(b2["h"]))
    inter = max(0.0, x2 - x1) * max(0.0, y2 - y1)
    if inter <= 0.0:
        return 0.0
    area1 = max(0.0, float(b1["w"])) * max(0.0, float(b1["h"]))
    area2 = max(0.0, float(b2["w"])) * max(0.0, float(b2["h"]))
    union = area1 + area2 - inter
    return round(inter / union, 4) if union > 1e-8 else 0.0


def _fallback_inspect_single(img_path: Path) -> Dict[str, Any]:
    """非 macOS 或 Vision CLI 不可用时的纯 Python 梯度显著性降级检测。"""
    try:
        with Image.open(img_path) as im:
            w, h = im.size
            small = im.convert("L").resize((120, max(12, int(round(120 * h / max(1, w))))))
            arr = np.asarray(small, dtype=np.float32)
    except Exception:
        return {"file": str(img_path), "width": 0, "height": 0, "faces": [], "saliency": None, "texts": [], "backend": "fallback"}

    gx = np.abs(np.diff(arr, axis=1, prepend=arr[:, :1]))
    gy = np.abs(np.diff(arr, axis=0, prepend=arr[:1, :]))
    grad = gx + gy
    thresh = float(np.percentile(grad, 82))
    ys, xs = np.where(grad >= thresh)
    sal = None
    if len(xs) > 10:
        sh, sw = arr.shape
        x0 = float(np.percentile(xs, 10)) / sw
        x1 = float(np.percentile(xs, 90)) / sw
        y0 = float(np.percentile(ys, 10)) / sh
        y1 = float(np.percentile(ys, 90)) / sh
        sal = {"x": round(x0, 4), "y": round(y0, 4), "w": round(max(0.05, x1 - x0), 4), "h": round(max(0.05, y1 - y0), 4)}
    return {
        "file": str(img_path),
        "width": int(w),
        "height": int(h),
        "faces": [],
        "saliency": sal,
        "texts": [],
        "backend": "numpy_gradient_fallback",
    }


def inspect_images(image_paths: List[Path], batch_size: int = 32) -> Dict[str, Dict[str, Any]]:
    """
    批量检测图片的人脸、双眼锚点/睁眼比、主体显著性与 OCR 文字框。
    返回映射: {str(path): result_dict}
    """
    if not image_paths:
        return {}
    vision_bin = ensure_vision_inspector_bin()
    results: Dict[str, Dict[str, Any]] = {}

    if vision_bin is not None:
        for i in range(0, len(image_paths), batch_size):
            chunk = image_paths[i : i + batch_size]
            cmd = [str(vision_bin)] + [str(p) for p in chunk]
            res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
            if res.returncode == 0:
                for line in (res.stdout or "").splitlines():
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        obj = json.loads(line)
                        obj["backend"] = "apple_vision"
                        results[obj["file"]] = obj
                    except Exception:
                        continue
            else:
                print(f"[vision_inspector] Apple Vision 执行失败 ({res.returncode}): {(res.stderr or '')[-300:]}", file=sys.stderr)

    for p in image_paths:
        key = str(p)
        if key not in results:
            results[key] = _fallback_inspect_single(p)
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="跨平台视觉原材分析器 (人脸/双眼/显著性/OCR)")
    parser.add_argument("images", nargs="+", help="待检测图片路径列表")
    args = parser.parse_args()
    paths = [Path(x) for x in args.images]
    out = inspect_images(paths)
    print(json.dumps(out, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
