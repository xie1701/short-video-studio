#!/usr/bin/env node
/**
 * capture_html_video.mjs
 * 确定性 HTML / Canvas / SVG / HyperFrames 无头浏览器逐帧抓取与 FFmpeg H.264/BT.709 编码桥
 * 契约：页面就绪置位 window.READY = true，并暴露 window.seek(t) 或 window.__timelines.main.seek(t)。
 * 用法：
 *   1) 单任务：
 *      node capture_html_video.mjs <project_dir> [--page index.html] [--fps 30] [--dur 12]
 *        [--size 1080x1920] [--out out.mp4] [--start 0] [--frames N] [--jpeg] [--smoke-json smoke.json]
 *   2) 单浏览器实例多幕批处理：
 *      node capture_html_video.mjs --batch-json <batch_manifest.json>
 */

import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn, spawnSync } from "node:child_process";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const argv = process.argv.slice(2);
const opt = (key, defVal = null) => {
  const idx = argv.indexOf(`--${key}`);
  if (idx < 0) return defVal;
  const nxt = argv[idx + 1];
  if (!nxt || nxt.startsWith("--")) return true;
  return nxt;
};

const batchJsonOpt = opt("batch-json", null);
if (!batchJsonOpt && (!argv.length || argv[0].startsWith("-"))) {
  console.error(
    "Usage:\n  node capture_html_video.mjs <project_dir> [--page index.html] [--fps 30] [--dur SEC] [--size 1080x1920] [--out out.mp4] [--start 0] [--frames N] [--jpeg] [--smoke-json smoke.json]\n  node capture_html_video.mjs --batch-json <batch.json>"
  );
  process.exit(2);
}

const MIME_MAP = {
  ".html": "text/html; charset=utf-8",
  ".htm": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".mjs": "text/javascript; charset=utf-8",
  ".css": "text/css; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".png": "image/png",
  ".jpg": "image/jpeg",
  ".jpeg": "image/jpeg",
  ".webp": "image/webp",
  ".svg": "image/svg+xml",
  ".otf": "font/otf",
  ".ttf": "font/ttf",
  ".woff": "font/woff",
  ".woff2": "font/woff2",
  ".mp4": "video/mp4",
  ".webm": "video/webm",
  ".wav": "audio/wav",
  ".m4a": "audio/mp4",
  ".mp3": "audio/mpeg",
};

function findChromeExecutable() {
  const envPath = process.env.SVS_CHROME_PATH;
  if (envPath && fs.existsSync(envPath)) return envPath;
  const cands = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "C:\\Program Files\\Google\\Chrome\\Application\\chrome.exe",
    "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe",
  ];
  for (const c of cands) {
    if (fs.existsSync(c)) return c;
  }
  return null;
}

async function loadBrowserLib(seedDir) {
  const pkgNames = ["playwright-core", "playwright"];
  const searchDirs = [
    ...(seedDir ? [seedDir] : []),
    path.join(os.homedir(), ".short-video-studio", "node_tools"),
  ];
  for (const baseDir of searchDirs) {
    const pkgJson = path.join(baseDir, "package.json");
    const modBase = path.join(baseDir, "node_modules");
    if (!fs.existsSync(modBase)) continue;
    try {
      const req = createRequire(fs.existsSync(pkgJson) ? pkgJson : path.join(modBase, "_anchor.js"));
      for (const name of pkgNames) {
        try {
          const resolved = req.resolve(name);
          const mod = await import(pathToFileURL(resolved).href);
          return mod.default || mod;
        } catch {}
      }
    } catch {}
  }
  for (const name of pkgNames) {
    try {
      const mod = await import(name);
      return mod.default || mod;
    } catch {}
  }
  return null;
}

function measureSmokePixels(mp4File, frameCount) {
  const probe = spawnSync(
    "ffmpeg",
    ["-v", "error", "-i", mp4File, "-vf", "scale=64:64,format=gray", "-f", "rawvideo", "-"],
    { maxBuffer: 64 * 1024 * 1024 }
  );
  if (probe.status !== 0 || !probe.stdout || !probe.stdout.length) {
    return { nonBlackFrames: 0, meanLumas: [] };
  }
  const buf = probe.stdout;
  const pxPerFrame = 64 * 64;
  const actualFrames = Math.floor(buf.length / pxPerFrame);
  const meanLumas = [];
  let nonBlackFrames = 0;
  for (let i = 0; i < Math.min(actualFrames, frameCount); i++) {
    let sum = 0;
    const base = i * pxPerFrame;
    for (let k = 0; k < pxPerFrame; k++) sum += buf[base + k];
    const mean = sum / pxPerFrame;
    meanLumas.push(Number(mean.toFixed(2)));
    if (mean >= 4.0) nonBlackFrames += 1;
  }
  return { nonBlackFrames, meanLumas };
}

function normalizeTask(raw) {
  const projectDir = path.resolve(String(raw.projectDir || raw.project_dir));
  const pageName = String(raw.page || "index.html");
  const fps = Math.max(1, Number(raw.fps || 30) || 30);
  const sizeStr = String(raw.size || "1080x1920");
  const [W, H] = sizeStr.split("x").map((n) => Math.max(2, Number(n) || 0));
  if (W % 2 !== 0 || H % 2 !== 0) {
    throw new Error(`Width and height must be even, got ${W}x${H}`);
  }
  const outOpt = String(raw.out || path.join(projectDir, "out", "code_render.mp4"));
  const outPath = path.isAbsolute(outOpt) ? outOpt : path.resolve(process.cwd(), outOpt);
  const durOpt = raw.dur !== undefined && raw.dur !== null ? Number(raw.dur) : null;
  const startFrame = Math.max(0, Number(raw.start || 0) || 0);
  const framesOpt = raw.frames !== undefined && raw.frames !== null ? Number(raw.frames) : null;
  const useJpeg = Boolean(raw.jpeg);
  const smokeOpt = raw.smokeJson || raw.smoke_json || null;
  const smokePath = smokeOpt
    ? path.isAbsolute(String(smokeOpt))
      ? String(smokeOpt)
      : path.resolve(process.cwd(), String(smokeOpt))
    : null;
  return { projectDir, pageName, fps, W, H, outPath, durOpt, startFrame, framesOpt, useJpeg, smokePath,
    smokeCheckpoints: Boolean(raw.smokeCheckpoints) };
}

async function captureSingleTask(browser, task, chromeBin) {
  const { projectDir, pageName, fps, W, H, outPath, durOpt, startFrame, framesOpt, useJpeg, smokePath, smokeCheckpoints } = task;
  const server = http.createServer((req, res) => {
    const rawUrl = decodeURIComponent((req.url || "/").split("?")[0]);
    if (rawUrl === "/favicon.ico") {
      res.writeHead(204);
      res.end();
      return;
    }
    const relPath = rawUrl === "/" ? pageName : rawUrl.replace(/^\/+/, "");
    const filePath = path.resolve(projectDir, relPath);
    if (
      !(filePath === projectDir || filePath.startsWith(projectDir + path.sep)) ||
      !fs.existsSync(filePath) ||
      fs.statSync(filePath).isDirectory()
    ) {
      res.writeHead(404);
      res.end("Not Found");
      return;
    }
    const ext = path.extname(filePath).toLowerCase();
    res.writeHead(200, { "Content-Type": MIME_MAP[ext] || "application/octet-stream" });
    fs.createReadStream(filePath).pipe(res);
  });

  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const port = server.address().port;
  const targetUrl = `http://127.0.0.1:${port}/${pageName.replace(/^\/+/, "")}`;

  const errors = [];
  let ff = null;
  let context = null;
  let framesRendered = 0;
  let totalFrames = 0;
  let checkpoints = null;
  const tStart = Date.now();

  try {
    context = await browser.newContext({ viewport: { width: W, height: H }, deviceScaleFactor: 1 });
    const page = await context.newPage();

    page.on("pageerror", (err) => errors.push(`pageerror: ${String(err)}`));
    page.on("console", (msg) => {
      if (msg.type() === "error") errors.push(`console.error: ${msg.text()}`);
    });
    page.on("requestfailed", (req) => {
      const rUrl = req.url();
      if (rUrl.startsWith(`http://127.0.0.1:${port}`)) {
        errors.push(`requestfailed: ${rUrl} (${req.failure()?.errorText || "unknown"})`);
      }
    });
    page.on("response", (res) => {
      const rUrl = res.url();
      if (res.status() >= 400 && rUrl.startsWith(`http://127.0.0.1:${port}`) && !rUrl.endsWith("/favicon.ico")) {
        errors.push(`http ${res.status()}: ${rUrl}`);
      }
    });

    await page.goto(targetUrl, { waitUntil: "domcontentloaded", timeout: 60000 });
    await page.waitForFunction(
      () => {
        const hasSeek =
          typeof window.seek === "function" ||
          (window.__timelines && window.__timelines.main && typeof window.__timelines.main.seek === "function");
        return Boolean(hasSeek && window.READY === true);
      },
      null,
      { timeout: 60000 }
    );

    const pageDur = await page.evaluate(() => Number(window.DUR || 0));
    const effDur = Number(durOpt || pageDur || 3.0);
    totalFrames = framesOpt ? Math.max(1, Number(framesOpt)) : Math.max(1, Math.round(effDur * fps) - startFrame);
    if (smokeCheckpoints) {
      const lastFrame = Math.max(0, Math.round(effDur * fps) - 1);
      checkpoints = [...new Set([0, Math.floor(lastFrame / 2), lastFrame])].map((frame) => frame / fps);
      totalFrames = checkpoints.length;
    }

    fs.mkdirSync(path.dirname(outPath), { recursive: true });
    ff = spawn(
      "ffmpeg",
      [
        "-v", "error", "-y",
        "-f", "image2pipe", "-framerate", String(fps), "-i", "-",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-pix_fmt", "yuv420p", "-color_range", "tv", "-colorspace", "bt709", "-color_primaries", "bt709", "-color_trc", "bt709",
        "-movflags", "+faststart", "-an",
        outPath,
      ],
      { stdio: ["pipe", "pipe", "pipe"] }
    );

    let ffStderr = "";
    ff.stderr.on("data", (d) => {
      ffStderr += d.toString();
    });

    const shotOpts = useJpeg ? { type: "jpeg", quality: 96 } : { type: "png" };
    for (let idx = 0; idx < totalFrames; idx++) {
      const t = checkpoints ? checkpoints[idx] : (startFrame + idx) / fps;
      await page.evaluate(async (curT) => {
        if (typeof window.seek === "function") {
          await window.seek(curT);
        } else if (window.__timelines && window.__timelines.main) {
          window.__timelines.main.seek(curT);
        }
      }, t);

      const buf = await page.screenshot(shotOpts);
      if (!ff.stdin.write(buf)) {
        await new Promise((res) => ff.stdin.once("drain", res));
      }
      framesRendered += 1;
      if (errors.length > 0) break;
    }

    ff.stdin.end();
    const ffExit = await new Promise((res) => ff.on("close", res));
    if (ffExit !== 0) {
      errors.push(`ffmpeg exit=${ffExit}: ${ffStderr.slice(-400)}`);
    }
  } catch (exc) {
    errors.push(`capture_exception: ${String(exc)}`);
  } finally {
    if (context) await context.close().catch(() => {});
    server.close();
  }

  const elapsedMs = Date.now() - tStart;
  const avgMsPerFrame = framesRendered > 0 ? Number((elapsedMs / framesRendered).toFixed(1)) : 0;

  if (smokePath) {
    const pxRes = fs.existsSync(outPath) ? measureSmokePixels(outPath, framesRendered) : { nonBlackFrames: 0, meanLumas: [] };
    const smokeReport = {
      ok: errors.length === 0 && framesRendered === totalFrames && totalFrames > 0 && pxRes.nonBlackFrames === totalFrames,
      frames_rendered: framesRendered,
      expected_frames: totalFrames,
      non_black_frames: pxRes.nonBlackFrames,
      mean_lumas: pxRes.meanLumas,
      avg_ms_per_frame: avgMsPerFrame,
      checkpoints_sec: checkpoints,
      chrome_executable: chromeBin,
      errors,
    };
    fs.mkdirSync(path.dirname(smokePath), { recursive: true });
    fs.writeFileSync(smokePath, JSON.stringify(smokeReport, null, 2), "utf-8");
  }

  return { ok: errors.length === 0 && framesRendered > 0, framesRendered, totalFrames, avgMsPerFrame, outPath, errors };
}

let tasks = [];
if (batchJsonOpt) {
  const bPath = path.isAbsolute(String(batchJsonOpt)) ? String(batchJsonOpt) : path.resolve(process.cwd(), String(batchJsonOpt));
  const rawJson = JSON.parse(fs.readFileSync(bPath, "utf-8"));
  const arr = Array.isArray(rawJson) ? rawJson : rawJson.tasks || [];
  tasks = arr.map(normalizeTask);
} else {
  tasks = [
    normalizeTask({
      projectDir: argv[0],
      page: opt("page", "index.html"),
      fps: opt("fps", 30),
      size: opt("size", "1080x1920"),
      out: opt("out", path.join(path.resolve(argv[0]), "out", "code_render.mp4")),
      dur: opt("dur", null),
      start: opt("start", 0),
      frames: opt("frames", null),
      jpeg: Boolean(opt("jpeg", false)),
      smokeJson: opt("smoke-json", null),
      smokeCheckpoints: Boolean(opt("smoke-checkpoints", false)),
    }),
  ];
}

if (!tasks.length) {
  console.error("[capture_html] Empty task list");
  process.exit(2);
}

const pw = await loadBrowserLib(tasks[0].projectDir);
if (!pw || !pw.chromium) {
  console.error("[capture_html] 未安装 playwright-core/playwright。请运行 `python3 scripts/check_and_setup.py --init-browser`。");
  process.exit(3);
}

const chromeBin = findChromeExecutable();
const launchOpts = {
  headless: true,
  args: [
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--enable-unsafe-swiftshader",
    "--ignore-gpu-blocklist",
    "--no-sandbox",
  ],
};
if (chromeBin) launchOpts.executablePath = chromeBin;

let browser = null;
const results = [];
try {
  browser = await pw.chromium.launch(launchOpts);
  const concurrency = Math.min(2, tasks.length);
  let cursor = 0;
  async function worker() {
    while (cursor < tasks.length) {
      const myIdx = cursor++;
      const res = await captureSingleTask(browser, tasks[myIdx], chromeBin);
      results[myIdx] = res;
    }
  }
  await Promise.all(Array.from({ length: concurrency }, () => worker()));
} finally {
  if (browser) await browser.close().catch(() => {});
}

const failed = results.filter((r) => !r || !r.ok);
if (failed.length > 0) {
  const f0 = failed[0];
  console.error(
    `[capture_html] FAILED: frames=${f0.framesRendered}/${f0.totalFrames}, errors=${JSON.stringify(f0.errors)}`
  );
  process.exit(1);
}

for (const r of results) {
  console.log(`[capture_html] OK: ${r.framesRendered} frames (${r.avgMsPerFrame} ms/f) -> ${r.outPath}`);
}
