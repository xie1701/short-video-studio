import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "dev_tools"))

import team_ctl  # noqa: E402


def _init_temp_repo(base: Path) -> Path:
    repo = base / "demo_repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-b", "main"], cwd=repo, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Tester"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "tester@example.com"], cwd=repo, check=True)
    (repo / "AGENTS.md").write_text("# AGENTS\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src" / "a.py").write_text("VAL = 1\n", encoding="utf-8")
    (repo / "src" / "b.py").write_text("VAL = 2\n", encoding="utf-8")
    (repo / "TEAM" / "tasks").mkdir(parents=True)
    (repo / "TEAM" / "reports").mkdir(parents=True)
    (repo / "TEAM" / "tasks" / "T901_demo.md").write_text(
        "\n".join([
            "---",
            "task_id: T901",
            "title: Demo isolation task",
            "owner: codex",
            "branch: agent/codex/T901",
            "allowed_files:",
            "  - src/a.py",
            "forbidden_files:",
            "  - src/b.py",
            "report_path: TEAM/reports/T901_codex.md",
            "test_commands:",
            "  - \"{python} -c 'import sys; sys.exit(0)'\"",
            "---",
            "",
            "Only touch src/a.py.",
        ]) + "\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=repo, check=True, capture_output=True)
    return repo


class TeamCtlTests(unittest.TestCase):
    def test_frontmatter_parse_and_spawn_gate_workflow(self):
        with tempfile.TemporaryDirectory() as td:
            repo = _init_temp_repo(Path(td))
            task_p = team_ctl.resolve_task_file(repo, "T901")
            meta = team_ctl.parse_task_frontmatter(task_p)
            self.assertEqual(meta["task_id"], "T901")
            self.assertEqual(meta["owner"], "codex")
            self.assertEqual(meta["allowed_files"], ["src/a.py"])
            self.assertEqual(meta["forbidden_files"], ["src/b.py"])

            self.assertEqual(team_ctl.cmd_spawn(repo, "T901"), 0)
            wt_dir = team_ctl.worktree_dir_for(repo, "codex")
            self.assertTrue(wt_dir.is_dir())

            # 主仓 main 上禁止通过 gate
            self.assertNotEqual(team_ctl.cmd_gate(repo, "T901", target_dir=repo), 0)

            # 修改未授权文件应被 gate 拦截
            (wt_dir / "src" / "b.py").write_text("VAL = 99\n", encoding="utf-8")
            self.assertNotEqual(team_ctl.cmd_gate(repo, "T901", target_dir=wt_dir), 0)
            subprocess.run(["git", "checkout", "--", "src/b.py"], cwd=wt_dir, check=True)

            # 仅修改白名单文件 + 回执，应通过 gate、review、merge 与 clean
            (wt_dir / "src" / "a.py").write_text("VAL = 42\n", encoding="utf-8")
            rep_p = wt_dir / "TEAM" / "reports" / "T901_codex.md"
            rep_p.parent.mkdir(parents=True, exist_ok=True)
            rep_p.write_text("# Done\n", encoding="utf-8")
            self.assertEqual(team_ctl.cmd_prompt(repo, "T901", mode="gui"), 0)
            self.assertEqual(team_ctl.cmd_prompt(repo, "T901", mode="cli"), 0)
            self.assertEqual(team_ctl.cmd_gate(repo, "T901", target_dir=wt_dir), 0)
            subprocess.run(["git", "add", "."], cwd=wt_dir, check=True)
            subprocess.run(["git", "commit", "-m", "feat: T901"], cwd=wt_dir, check=True, capture_output=True)

            self.assertEqual(team_ctl.cmd_review(repo, "T901"), 0)
            self.assertEqual(team_ctl.cmd_merge(repo, "T901"), 0)
            self.assertIn("VAL = 42", (repo / "src" / "a.py").read_text(encoding="utf-8"))
            self.assertEqual(team_ctl.cmd_clean(repo, "T901"), 0)
            self.assertFalse(wt_dir.exists())


if __name__ == "__main__":
    unittest.main()
