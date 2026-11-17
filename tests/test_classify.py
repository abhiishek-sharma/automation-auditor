import unittest

from automation_doctor.checks import ERROR, FAIL, PASS, CheckResult
from automation_doctor.classify import OK, REPORTED_FAILURE, SILENT_FAILURE, UNVERIFIED, classify

from helpers import make_run


def res(status):
    return CheckResult(name=f"c-{status}", type="file", status=status, expected="e", actual="a")


class ClassifyTests(unittest.TestCase):
    def test_table(self):
        cases = [
            (dict(exit_code=0), [res(PASS)], OK),
            (dict(exit_code=0), [res(PASS), res(FAIL)], SILENT_FAILURE),
            (dict(exit_code=0), [res(ERROR), res(FAIL)], SILENT_FAILURE),  # a failure outranks an error
            (dict(exit_code=0), [res(PASS), res(ERROR)], UNVERIFIED),
            (dict(exit_code=0), [], UNVERIFIED),
            (dict(exit_code=1), [res(PASS)], REPORTED_FAILURE),
            (dict(exit_code=1), [res(FAIL)], REPORTED_FAILURE),
            (dict(exit_code=None, timed_out=True), [res(PASS)], REPORTED_FAILURE),
            (dict(exit_code=None, launch_error="FileNotFoundError"), [], REPORTED_FAILURE),
        ]
        for run_kw, results, expected in cases:
            with self.subTest(run=run_kw, statuses=[r.status for r in results]):
                self.assertEqual(classify(make_run(**run_kw), results).code, expected)

    def test_exit_codes_and_alert(self):
        v = classify(make_run(exit_code=0), [res(FAIL)])
        self.assertTrue(v.alert)
        self.assertEqual(v.exit_code, 1)
        ok = classify(make_run(exit_code=0), [res(PASS)])
        self.assertFalse(ok.alert)
        self.assertEqual(ok.exit_code, 0)
