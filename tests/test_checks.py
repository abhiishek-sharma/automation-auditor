import json
import os
import sqlite3
import time

from automation_doctor.checks import ERROR, FAIL, PASS, run_checks
from automation_doctor.config import ConfigError, job_from_dict

from helpers import JsonServer, TempDirCase


def check(spec, ctx):
    """Validate through the real config path, then evaluate."""
    job = job_from_dict({"name": "t", "command": "true", "checks": [spec]})
    return run_checks(job.checks, ctx)[0]


class FileCheckTests(TempDirCase):
    def write(self, name, content, age_s=0):
        p = self.dir / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        if age_s:
            t = time.time() - age_s
            os.utime(p, (t, t))
        return p

    def test_exists_and_json_field_pass(self):
        self.write("out/r.json", json.dumps({"total": 5, "items": [{"id": "a"}]}))
        r = check({"type": "file", "path": "out/r.json", "fresh": True, "json_field": "items.0.id", "op": "equals",
                   "value": "a"}, self.ctx())
        self.assertEqual(r.status, PASS, r.actual)

    def test_missing_file_lists_files_written_during_run(self):
        self.write("out/report-2026-20-11.json", "{}")
        self.write("out/report-2026-11-19.json", "{}", age_s=86400)
        r = check({"type": "file", "path": "out/report-2026-11-20.json"}, self.ctx())
        self.assertEqual(r.status, FAIL)
        nearby = r.evidence["nearby_files"]
        self.assertEqual(nearby[0]["name"], "report-2026-20-11.json")
        self.assertTrue(nearby[0]["modified_during_run"])
        self.assertFalse(nearby[1]["modified_during_run"])
        self.assertIn("report-2026-20-11.json", r.message)

    def test_stale_file_fails_freshness(self):
        self.write("out/r.json", "{}", age_s=3600)
        r = check({"type": "file", "path": "out/r.json", "fresh": True}, self.ctx())
        self.assertEqual(r.status, FAIL)
        self.assertIn("stale", r.actual)

    def test_freshness_without_start_time_is_error_not_pass(self):
        self.write("r.json", "{}")
        ctx = self.ctx()
        ctx.run.started_at = None
        r = check({"type": "file", "path": "r.json", "fresh": True}, ctx)
        self.assertEqual(r.status, ERROR)

    def test_json_field_missing_or_wrong(self):
        self.write("r.json", json.dumps({"total": 0}))
        self.assertEqual(check({"type": "file", "path": "r.json", "json_field": "total", "op": "gt", "value": 0},
                               self.ctx()).status, FAIL)
        self.assertEqual(check({"type": "file", "path": "r.json", "json_field": "regions", "op": "non_empty"},
                               self.ctx()).status, FAIL)

    def test_invalid_json_fails(self):
        self.write("r.json", "{not json")
        r = check({"type": "file", "path": "r.json", "json_field": "a"}, self.ctx())
        self.assertEqual(r.status, FAIL)
        self.assertIn("not valid JSON", r.actual)

    def test_contains(self):
        self.write("r.txt", "rows=10\nstatus=done")
        self.assertEqual(check({"type": "file", "path": "r.txt", "contains": "status=done"}, self.ctx()).status, PASS)
        self.assertEqual(check({"type": "file", "path": "r.txt", "contains": "nope"}, self.ctx()).status, FAIL)


class SqliteCheckTests(TempDirCase):
    def setUp(self):
        super().setUp()
        self.db = self.dir / "o.db"
        conn = sqlite3.connect(self.db)
        conn.execute("CREATE TABLE orders(order_id TEXT, amount REAL)")
        conn.executemany("INSERT INTO orders VALUES (?, ?)", [("A", 1), ("B", 2), ("A", 1)])
        conn.commit()
        conn.close()

    def test_duplicate_detection(self):
        r = check({"type": "sqlite", "db": "o.db",
                   "query": "SELECT order_id, COUNT(*) n FROM orders GROUP BY order_id HAVING n > 1",
                   "expect_rows": {"op": "equals", "value": 0}}, self.ctx())
        self.assertEqual(r.status, FAIL)
        self.assertEqual(r.evidence["rows_sample"], [["A", 2]])

    def test_scalar_with_params(self):
        r = check({"type": "sqlite", "db": "o.db", "query": "SELECT COUNT(*) FROM orders WHERE order_id = ?",
                   "params": ["B"], "expect_scalar": {"op": "equals", "value": 1}}, self.ctx())
        self.assertEqual(r.status, PASS, r.actual)

    def test_missing_db_is_fail(self):
        r = check({"type": "sqlite", "db": "nope.db", "query": "SELECT 1", "expect_rows": {"op": "gte", "value": 1}},
                  self.ctx())
        self.assertEqual(r.status, FAIL)

    def test_bad_sql_is_error(self):
        r = check({"type": "sqlite", "db": "o.db", "query": "SELECT * FROM missing_table",
                   "expect_rows": {"op": "gte", "value": 1}}, self.ctx())
        self.assertEqual(r.status, ERROR)

    def test_write_query_rejected_by_config(self):
        with self.assertRaises(ConfigError):
            job_from_dict({"name": "t", "command": "true", "checks": [
                {"type": "sqlite", "db": "o.db", "query": "DELETE FROM orders", "expect_rows": {"op": "equals", "value": 0}}]})

    def test_connection_is_read_only_even_if_validator_bypassed(self):
        # A CTE that writes would pass the "starts with WITH" rule; the read-only connection must still block it.
        job = job_from_dict({"name": "t", "command": "true", "checks": [
            {"type": "sqlite", "db": "o.db", "query": "SELECT 1", "expect_rows": {"op": "gte", "value": 1}}]})
        job.checks[0]["query"] = "DELETE FROM orders"
        r = run_checks(job.checks, self.ctx())[0]
        self.assertEqual(r.status, ERROR)
        conn = sqlite3.connect(self.db)
        self.assertEqual(conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0], 3)
        conn.close()


class HttpCheckTests(TempDirCase):
    def test_200_with_empty_items_fails(self):
        with JsonServer({"/api": (200, {"items": [], "status": "ok"})}) as s:
            r = check({"type": "http", "url": s.url + "/api", "json_field": "items", "op": "non_empty"}, self.ctx())
        self.assertEqual(r.status, FAIL)
        self.assertEqual(r.evidence["status"], 200)
        self.assertIn("HTTP 200", r.actual)

    def test_200_with_data_passes(self):
        with JsonServer({"/api": (200, {"items": [1]})}) as s:
            r = check({"type": "http", "url": s.url + "/api", "json_field": "items", "op": "non_empty"}, self.ctx())
        self.assertEqual(r.status, PASS)

    def test_wrong_status_fails(self):
        with JsonServer({"/api": (503, {"error": "down"})}) as s:
            r = check({"type": "http", "url": s.url + "/api"}, self.ctx())
        self.assertEqual(r.status, FAIL)

    def test_body_contains(self):
        with JsonServer({"/api": (200, {"error": "quota exceeded"})}) as s:
            r = check({"type": "http", "url": s.url + "/api", "contains": "\"items\""}, self.ctx())
        self.assertEqual(r.status, FAIL)

    def test_unreachable_is_error(self):
        r = check({"type": "http", "url": "http://127.0.0.1:9/x", "timeout_seconds": 1}, self.ctx())
        self.assertEqual(r.status, ERROR)


class OutputCheckTests(TempDirCase):
    def test_not_contains(self):
        ctx = self.ctx(stderr="Traceback (most recent call last):\nKeyError: 'x'")
        self.assertEqual(check({"type": "output", "stream": "stderr", "not_contains": "Traceback"}, ctx).status, FAIL)
        self.assertEqual(check({"type": "output", "matches": r"rows=\d+"}, self.ctx(stdout="rows=10")).status, PASS)


class ConfigValidationTests(TempDirCase):
    def test_rejects_unknown_check_type_and_bad_op(self):
        with self.assertRaises(ConfigError):
            job_from_dict({"name": "t", "command": "true", "checks": [{"type": "magic"}]})
        with self.assertRaises(ConfigError):
            job_from_dict({"name": "t", "command": "true", "checks": [
                {"type": "file", "path": "a", "json_field": "x", "op": "equals"}]})  # equals needs value
        with self.assertRaises(ConfigError):
            job_from_dict({"name": "../evil", "command": "true", "checks": [{"type": "file", "path": "a"}]})
