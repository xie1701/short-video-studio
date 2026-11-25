#!/usr/bin/env python3
"""
[多 AI Agent 协同控制闸门 · dev_tools/team_ctl.py]

基于 Git Worktree + 任务单 Frontmatter + 白名单机械闸门，管理多 Agent 并行研发闭环：
  spawn <task_id>           基于 main 建分支并创建外部 Worktree（.wt-<repo>/<owner>）
  prompt <task_id>          输出可直接投喂给 Worker 的自包含派单指令（--mode gui|cli）
  gate <task_id>            Worker 交卷前硬闸门（分支校验、allowed_files 白名单、隐私扫描、单测）
  review <task_id>          总控审核摘要（diffstat、越权检测、回执内容）
  merge <task_id>           审核通过后 --no-ff 合并入 main
  clean <task_id>           清理 Worktree 与已合并分支
"""

from __future__ import annotations

import argparse
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


DEFAULT_REPO_ROOT = Path(__file__).resolve().parent.parent
PRIVACY_PATH_RE = re.compile(r"/(?:Users|home)/[A-Za-z0-9._-]+|C:\\\\Users\\\\[A-Za-z0-9._-]+")
TEXT_SCAN_EXTS = {".py", ".md", ".json", ".cube", ".mjs", ".js", ".html", ".command", ".bat", ".sh", ".ps1", ".txt"}


def _git(cwd: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    res = subprocess.run(
        ["git", *args],
        cwd=str(cwd),
        capture_output=True,
        text=True,
    )
    if check and res.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed ({cwd}): {res.stderr.strip() or res.stdout.strip()}")
    return res


def _unquote(s: str) -> str:
    if len(s) >= 2 and ((s[0] == '"' and s[-1] == '"') or (s[0] == "'" and s[-1] == "'")):
        return s[1:-1]
    return s


def parse_task_frontmatter(task_path: Path) -> Dict[str, Any]:
    """解析 TEAM/tasks/<task_id>_*.md 顶部的 YAML 风格 Frontmatter。"""
    raw = task_path.read_text(encoding="utf-8")
    lines = raw.splitlines()
    if not lines or lines[0].strip() != "---":
        raise ValueError(f"任务单缺少 Frontmatter 起始标记 '---': {task_path}")
    end_idx = None
    for idx in range(1, len(lines)):
        if lines[idx].strip() == "---":
            end_idx = idx
            break
    if end_idx is None:
        raise ValueError(f"任务单缺少 Frontmatter 结束标记 '---': {task_path}")

    meta: Dict[str, Any] = {}
    cur_list_key: Optional[str] = None
    for line in lines[1:end_idx]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if stripped.startswith("- "):
            if cur_list_key is None:
                raise ValueError(f"任务单列表项无所属字段: {line!r} ({task_path})")
            val = _unquote(stripped[2:].strip())
            meta.setdefault(cur_list_key, []).append(val)
            continue
        if ":" in line:
            k, v = line.split(":", 1)
            key = k.strip()
            val_s = v.strip()
            if not val_s:
                meta[key] = []
                cur_list_key = key
            else:
                meta[key] = _unquote(val_s)
                cur_list_key = None
    meta["_task_file"] = str(task_path)
    meta["_rel_task_file"] = task_path.name
    return meta


def resolve_task_file(repo_root: Path, task_id: str) -> Path:
    tasks_dir = repo_root / "TEAM" / "tasks"
    if not tasks_dir.is_dir():
        raise FileNotFoundError(f"未找到任务单目录: {tasks_dir}")
    tid_up = task_id.strip()
    exact = list(tasks_dir.glob(f"{tid_up}*.md"))
    matches = [p for p in sorted(exact) if p.stem == tid_up or p.stem.startswith(f"{tid_up}_")]
    if not matches:
        raise FileNotFoundError(f"未找到匹配任务单 '{task_id}' (位置: {tasks_dir})")
    if len(matches) > 1:
        raise ValueError(f"任务单编号有歧义 '{task_id}': {[p.name for p in matches]}")
    return matches[0]


def worktree_base(repo_root: Path) -> Path:
    return repo_root.parent / f".wt-{repo_root.name}"


def worktree_dir_for(repo_root: Path, owner: str) -> Path:
    safe_owner = re.sub(r"[^A-Za-z0-9._-]", "_", owner.strip()) or "worker"
    return worktree_base(repo_root) / safe_owner


def _is_git_clean(cwd: Path) -> Tuple[bool, str]:
    res = _git(cwd, "status", "--porcelain")
    dirty = res.stdout.strip()
    return (not dirty), dirty


def _collect_branch_changed_files(cwd: Path, base_ref: str = "main") -> List[str]:
    changed = set()
    # 1) 已提交相对于 base_ref 的变更
    base_chk = _git(cwd, "rev-parse", "--verify", base_ref, check=False)
    if base_chk.returncode == 0:
        diff_committed = _git(cwd, "diff", "--name-only", f"{base_ref}...HEAD")
        for line in diff_committed.stdout.splitlines():
            if line.strip():
                changed.add(line.strip())
    # 2) 工作区与暂存区改动
    for args in (("diff", "--name-only"), ("diff", "--cached", "--name-only"), ("ls-files", "--others", "--exclude-standard")):
        out = _git(cwd, *args).stdout
        for line in out.splitlines():
            if line.strip():
                changed.add(line.strip())
    return sorted(changed)


def _check_privacy_leaks(cwd: Path, files: List[str]) -> List[str]:
    leaks: List[str] = []
    for rel in files:
        fp = cwd / rel
        if not fp.is_file() or fp.suffix.lower() not in TEXT_SCAN_EXTS:
            continue
        txt = fp.read_text(encoding="utf-8", errors="ignore")
        m = PRIVACY_PATH_RE.search(txt)
        if m:
            leaks.append(f"{rel}: 检测到本机路径片段 {m.group(0)!r}")
    return leaks


_IGNORED_LOCAL_ASSET_GLOBS = (
    "forensic/*.json",
    "short-video-studio/assets/luts/style_*.cube",
    "short-video-studio/assets/fonts/jianying",
    "short-video-studio/assets/sound_vibe/bgm/*.mp3",
    "short-video-studio/assets/sound_vibe/bgm/*.m4a",
    "short-video-studio/assets/sound_vibe/sfx/style_*",
)


def _sync_ignored_local_assets(repo_root: Path, wt_dir: Path) -> int:
    """将主仓 .gitignore 忽略的本地只读资产软链到 Worktree，方便 Worker 跑通全量测试。"""
    linked = 0
    seen: set[str] = set()
    for pattern in _IGNORED_LOCAL_ASSET_GLOBS:
        for src in sorted(repo_root.glob(pattern)):
            rel = src.relative_to(repo_root).as_posix()
            if rel in seen:
                continue
            seen.add(rel)
            if _git(repo_root, "check-ignore", "-q", rel, check=False).returncode != 0:
                continue
            dst = wt_dir / rel
            if dst.exists() or dst.is_symlink():
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            os.symlink(src, dst)
            linked += 1
    return linked


def cmd_spawn(repo_root: Path, task_id: str) -> int:
    task_file = resolve_task_file(repo_root, task_id)
    meta = parse_task_frontmatter(task_file)
    owner = str(meta.get("owner") or "").strip()
    branch = str(meta.get("branch") or "").strip()
    if not owner or not branch:
        raise ValueError(f"任务单缺少 owner 或 branch 字段: {task_file}")

    cur_branch = _git(repo_root, "branch", "--show-current").stdout.strip()
    if cur_branch != "main":
        raise RuntimeError(f"请在 main 分支执行 spawn（当前在 {cur_branch!r}）")
    clean, dirty = _is_git_clean(repo_root)
    if not clean:
        raise RuntimeError(f"主仓存在未提交改动，请先清理或提交后再建 Worktree：\n{dirty}")

    wt_dir = worktree_dir_for(repo_root, owner)
    wt_dir.parent.mkdir(parents=True, exist_ok=True)
    if wt_dir.exists():
        wt_cur = _git(wt_dir, "branch", "--show-current", check=False).stdout.strip()
        if wt_cur == branch:
            linked = _sync_ignored_local_assets(repo_root, wt_dir)
            print(f"[spawn] Worktree 已就绪: {wt_dir} (branch: {branch}, 软链只读资产 {linked} 项)")
            return 0
        wt_clean, wt_dirty = _is_git_clean(wt_dir)
        if not wt_clean:
            raise RuntimeError(f"已有 Worktree {wt_dir} ({wt_cur}) 存在未提交内容，请先处理：\n{wt_dirty}")
        _git(repo_root, "worktree", "remove", str(wt_dir))

    branch_exists = _git(repo_root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0
    if branch_exists:
        _git(repo_root, "worktree", "add", str(wt_dir), branch)
    else:
        _git(repo_root, "worktree", "add", "-b", branch, str(wt_dir), "main")

    linked = _sync_ignored_local_assets(repo_root, wt_dir)
    print(f"[spawn] 已就绪: {wt_dir}")
    print(f"        分支:   {branch}")
    print(f"        任务单: {task_file.relative_to(repo_root)}")
    print(f"        资产链: 软链本地只读资产 {linked} 项")
    return 0


def cmd_prompt(repo_root: Path, task_id: str, mode: str = "gui") -> int:
    task_file = resolve_task_file(repo_root, task_id)
    meta = parse_task_frontmatter(task_file)
    owner = str(meta.get("owner") or "")
    branch = str(meta.get("branch") or "")
    rel_task = str(task_file.relative_to(repo_root))
    report_path = str(meta.get("report_path") or f"TEAM/reports/{task_id}_{owner}.md")
    wt_dir = worktree_dir_for(repo_root, owner)

    prompt_text = (
        f"请新开空白会话，在目录 {wt_dir}（必须先运行 git branch --show-current 确认在分支 {branch}）作业。\n"
        f"先完整阅读根目录 AGENTS.md 与 {rel_task}，严格只修改 allowed_files 允许的文件与 {report_path}。\n"
        f"完成后运行 \"$HOME/.short-video-studio/venv/bin/python\" dev_tools/team_ctl.py gate {meta.get('task_id', task_id)}，"
        f"见 GATE PASS 后按 TEAM/REPORT_TEMPLATE.md 写好 {report_path} 并在本分支执行 git commit（禁止 git push），然后回复我已交卷。"
    )
    if mode == "cli":
        cli_cmd = f"codex exec -C {shlex.quote(str(wt_dir))} {shlex.quote(prompt_text)}"
        print(f"# [{meta.get('task_id', task_id)} · {owner}] Codex CLI 无头触发预览（配置好 codex 后可用）：")
        print(cli_cmd)
    else:
        print(f"=== 复制粘贴给 [{owner}] ({meta.get('task_id', task_id)}) ===")
        print(prompt_text)
        print("=================================================")
    return 0


def cmd_gate(repo_root: Path, task_id: str, target_dir: Optional[Path] = None, skip_tests: bool = False) -> int:
    cwd = (target_dir or Path.cwd()).resolve()
    task_file = resolve_task_file(cwd if (cwd / "TEAM").is_dir() else repo_root, task_id)
    meta = parse_task_frontmatter(task_file)
    expected_branch = str(meta.get("branch") or "").strip()
    cur_branch = _git(cwd, "branch", "--show-current").stdout.strip()

    failures: List[str] = []
    if not expected_branch:
        failures.append("任务单缺少 branch 配置")
    elif cur_branch != expected_branch:
        failures.append(f"分支不匹配：当前在 {cur_branch!r}，要求必须在 {expected_branch!r}（严禁在 main 直接作业）")

    allowed = {str(p).strip() for p in (meta.get("allowed_files") or []) if str(p).strip()}
    report_path = str(meta.get("report_path") or "").strip()
    if report_path:
        allowed.add(report_path)
    forbidden = {str(p).strip() for p in (meta.get("forbidden_files") or []) if str(p).strip()}

    changed = _collect_branch_changed_files(cwd, base_ref="main")
    if not changed:
        failures.append("当前分支相对 main 尚无任何文件改动")

    unauthorized = [f for f in changed if f not in allowed]
    if unauthorized:
        failures.append(f"检测到越权修改文件（不在 allowed_files 白名单内）: {unauthorized}")
    touched_forbidden = [f for f in changed if f in forbidden]
    if touched_forbidden:
        failures.append(f"触碰 forbidden_files 禁区文件: {touched_forbidden}")

    leaks = _check_privacy_leaks(cwd, changed)
    if leaks:
        failures.extend(leaks)

    if failures:
        print(f"[GATE FAIL] {meta.get('task_id', task_id)}:")
        for item in failures:
            print(f"  - {item}")
        return 1

    if not skip_tests:
        test_cmds = [str(c).strip() for c in (meta.get("test_commands") or []) if str(c).strip()]
        for raw_cmd in test_cmds:
            cmd = raw_cmd.replace("{python}", sys.executable)
            print(f"[gate] running: {cmd}")
            res = subprocess.run(cmd, cwd=str(cwd), shell=True)
            if res.returncode != 0:
                print(f"[GATE FAIL] 测试命令失败 (exit={res.returncode}): {cmd}")
                return res.returncode or 1

    print(f"[gate] 分支校验通过: {cur_branch}")
    print(f"[gate] 变更白名单通过 ({len(changed)} 个文件): {changed}")
    print(f"=== GATE PASS: {meta.get('task_id', task_id)} ===")
    return 0


def cmd_review(repo_root: Path, task_id: str) -> int:
    task_file = resolve_task_file(repo_root, task_id)
    meta = parse_task_frontmatter(task_file)
    branch = str(meta.get("branch") or "").strip()
    owner = str(meta.get("owner") or "").strip()
    report_rel = str(meta.get("report_path") or f"TEAM/reports/{task_id}_{owner}.md")
    wt_dir = worktree_dir_for(repo_root, owner)

    if _git(repo_root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode != 0:
        raise RuntimeError(f"分支尚不存在: {branch}")

    print(f"=== REVIEW: {meta.get('task_id', task_id)} ({branch}) ===")
    commits = _git(repo_root, "log", "--oneline", f"main..{branch}").stdout.strip()
    print("[Commits]")
    print(commits or "  (暂无新提交)")
    diffstat = _git(repo_root, "diff", "--stat", f"main...{branch}").stdout.strip()
    print("\n[Diffstat vs merge-base(main)]")
    print(diffstat or "  (无差异)")

    changed = [
        line.strip()
        for line in _git(repo_root, "diff", "--name-only", f"main...{branch}").stdout.splitlines()
        if line.strip()
    ]
    allowed = {str(p).strip() for p in (meta.get("allowed_files") or []) if str(p).strip()}
    allowed.add(report_rel)
    unauthorized = [f for f in changed if f not in allowed]
    print(f"\n[Whitelist Audit] changed={len(changed)}, unauthorized={unauthorized}")

    report_candidates = [wt_dir / report_rel, repo_root / report_rel]
    found_report = next((p for p in report_candidates if p.is_file()), None)
    if not found_report:
        show_res = _git(repo_root, "show", f"{branch}:{report_rel}", check=False)
        if show_res.returncode == 0:
            print(f"\n[Report ({branch}:{report_rel})]")
            print(show_res.stdout.strip())
        else:
            print(f"\n[Report] 尚未提交回执: {report_rel}")
    else:
        print(f"\n[Report ({found_report})]")
        print(found_report.read_text(encoding="utf-8").strip())
    return 0


def cmd_merge(repo_root: Path, task_id: str) -> int:
    task_file = resolve_task_file(repo_root, task_id)
    meta = parse_task_frontmatter(task_file)
    branch = str(meta.get("branch") or "").strip()
    report_rel = str(meta.get("report_path") or "").strip()

    cur_branch = _git(repo_root, "branch", "--show-current").stdout.strip()
    if cur_branch != "main":
        raise RuntimeError(f"只能在 main 分支执行 merge（当前在 {cur_branch!r}）")
    clean, dirty = _is_git_clean(repo_root)
    if not clean:
        raise RuntimeError(f"主仓存在未提交改动，拒绝 merge：\n{dirty}")

    changed = [
        line.strip()
        for line in _git(repo_root, "diff", "--name-only", f"main...{branch}").stdout.splitlines()
        if line.strip()
    ]
    if not changed:
        raise RuntimeError(f"分支 {branch} 相对 main 无提交内容")
    allowed = {str(p).strip() for p in (meta.get("allowed_files") or []) if str(p).strip()}
    if report_rel:
        allowed.add(report_rel)
    unauthorized = [f for f in changed if f not in allowed]
    if unauthorized:
        raise RuntimeError(f"拒绝合并！分支 {branch} 存在白名单外文件: {unauthorized}")

    title = str(meta.get("title") or task_id).strip()
    _git(repo_root, "merge", "--no-ff", branch, "-m", f"merge({meta.get('task_id', task_id)}): {title}")
    print(f"[merge] 已将 {branch} 合并至 main。")
    return 0


def cmd_clean(repo_root: Path, task_id: str) -> int:
    task_file = resolve_task_file(repo_root, task_id)
    meta = parse_task_frontmatter(task_file)
    owner = str(meta.get("owner") or "").strip()
    branch = str(meta.get("branch") or "").strip()
    wt_dir = worktree_dir_for(repo_root, owner)
    if wt_dir.exists():
        _git(repo_root, "worktree", "remove", str(wt_dir))
        print(f"[clean] 已移除 Worktree: {wt_dir}")
    if _git(repo_root, "show-ref", "--verify", f"refs/heads/{branch}", check=False).returncode == 0:
        _git(repo_root, "branch", "-d", branch)
        print(f"[clean] 已删除已合并分支: {branch}")
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="多 AI Agent 协同工作台控制工具")
    parser.add_argument("--repo-root", default=None, help="主仓库根目录（默认自动定位）")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_spawn = sub.add_parser("spawn", help="创建 Worker 专属分支与外部 Worktree")
    p_spawn.add_argument("task_id")

    p_prompt = sub.add_parser("prompt", help="生成派发给 Worker 的提示词/CLI 命令")
    p_prompt.add_argument("task_id")
    p_prompt.add_argument("--mode", choices=("gui", "cli"), default="gui")

    p_gate = sub.add_parser("gate", help="Worker 交卷机械闸门")
    p_gate.add_argument("task_id")
    p_gate.add_argument("--repo-dir", default=None)
    p_gate.add_argument("--skip-tests", action="store_true")

    p_review = sub.add_parser("review", help="总控查看任务分支 diff 与回执")
    p_review.add_argument("task_id")

    p_merge = sub.add_parser("merge", help="审核通过后合并至 main")
    p_merge.add_argument("task_id")

    p_clean = sub.add_parser("clean", help="清理 Worktree 与已合并分支")
    p_clean.add_argument("task_id")

    args = parser.parse_args(argv)
    repo_root = Path(args.repo_root).resolve() if args.repo_root else DEFAULT_REPO_ROOT

    if args.cmd == "spawn":
        return cmd_spawn(repo_root, args.task_id)
    if args.cmd == "prompt":
        return cmd_prompt(repo_root, args.task_id, mode=args.mode)
    if args.cmd == "gate":
        return cmd_gate(
            repo_root,
            args.task_id,
            target_dir=(Path(args.repo_dir).resolve() if args.repo_dir else None),
            skip_tests=args.skip_tests,
        )
    if args.cmd == "review":
        return cmd_review(repo_root, args.task_id)
    if args.cmd == "merge":
        return cmd_merge(repo_root, args.task_id)
    if args.cmd == "clean":
        return cmd_clean(repo_root, args.task_id)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
