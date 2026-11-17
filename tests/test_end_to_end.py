"""Run the real CLI and the full demo scenario set (no model needed)."""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from automation_doctor.cli import main
from automation_doctor.demo import run_demo

ROOT = Path(__file__).resolve().parents[1]


class DemoTests(unittest.TestCase):
    def test_every_scenario_classified_as_labelled(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "demo"
            rc = run_demo(provider="rules", out=out)
            summary = json.loads((out / "summary.json").read_text())
        wrong = [(c["job"], c["expected_verdict"], c["verdict"]) for c in summary["cases"] if not c["verdict_matches"]]
        self.assertEqual(wrong, [])
        self.assertEqual(rc, 0)
        self.assertGreaterEqual(summary["metrics"]["cases"], 8)
        self.assertEqual(summary["metrics"]["false_alerts"], 0)
        by_job = {c["job"]: c for c in summary["cases"]}
        for name in ("02_wrong_file", "03_http_missing_data", "04_duplicate_on_retry"):
            self.assertEqual(by_job[name]["exit_code_reported"], 0)  # the job claimed success
            self.assertEqual(by_job[name]["verdict"], "SILENT_FAILURE")
        self.assertEqual(by_job["01_healthy_report"]["verdict"], "OK")
        self.assertEqual(by_job["01_healthy_report"]["diagnosis_provider"], "")  # no alert, no diagnosis

    def test_demo_refuses_to_wipe_unrelated_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "precious.txt").write_text("keep me")
            with self.assertRaises(ValueError):
                run_demo(provider="rules", out=Path(tmp))
            self.assertTrue((Path(tmp) / "precious.txt").exists())


class CliTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def spec(self, command, checks):
        p = self.dir / "job.json"
        p.write_text(json.dumps({"name": "cli-job", "command": command, "checks": checks}))
        return str(p)

    def cli(self, *args):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "src"), "AD_PROVIDER": "rules"}
        return subprocess.run([sys.executable, "-m", "automation_doctor", *args], capture_output=True, text=True,
                              env=env, cwd=self.dir)

    def test_run_exit_codes(self):
        ok = self.spec([sys.executable, "-c", "open('o.txt','w').write('done')"],
                       [{"type": "file", "path": "o.txt", "fresh": True, "contains": "done"}])
        self.assertEqual(self.cli("run", ok, "--no-save").returncode, 0)
        silent = self.spec([sys.executable, "-c", "print('ok')"], [{"type": "file", "path": "o2.txt"}])
        r = self.cli("run", silent, "--out", str(self.dir / "rep"))
        self.assertEqual(r.returncode, 1, r.stdout + r.stderr)
        self.assertIn("SILENT_FAILURE", r.stdout)
        self.assertTrue(list((self.dir / "rep").glob("*.html")))
        loud = self.spec([sys.executable, "-c", "raise SystemExit(3)"], [{"type": "file", "path": "o.txt"}])
        self.assertEqual(self.cli("run", loud, "--no-save").returncode, 2)

    def test_inspect_with_run_record_and_json_output(self):
        spec = self.spec("true", [{"type": "output", "stream": "stderr", "not_contains": "ERROR"}])
        rec = self.dir / "run.json"
        rec.write_text(json.dumps({"exit_code": 0, "stderr": "ERROR: upstream refused", "started_at": 1}))
        r = self.cli("inspect", spec, "--run-record", str(rec), "--format", "json", "--no-save")
        self.assertEqual(r.returncode, 1, r.stderr)
        self.assertEqual(json.loads(r.stdout)["verdict"]["code"], "SILENT_FAILURE")

    def test_config_error_exit_code(self):
        bad = self.dir / "bad.json"
        bad.write_text("{\"name\": \"x\"}")
        self.assertEqual(main(["validate", str(bad)]), 4)
        self.assertEqual(main(["validate", str(ROOT / "demo/jobs/04_duplicate_on_retry.json"),
                               "--var", "WORKSPACE=/tmp/x", "--var", "DEMO_API_URL=http://127.0.0.1:1"]), 0)


class YamlSpecTests(unittest.TestCase):
    def test_yaml_example_runs_when_pyyaml_installed(self):
        try:
            import yaml  # noqa: F401
        except ImportError:
            self.skipTest("PyYAML not installed (optional extra)")
        with tempfile.TemporaryDirectory() as tmp:
            rc = main(["run", str(ROOT / "examples/nightly_backup.yaml"), "--var", f"WORKSPACE={tmp}",
                       "--provider", "rules", "--no-save"])
        self.assertEqual(rc, 0)
