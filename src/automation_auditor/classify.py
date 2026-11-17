"""Turn a run's reported status plus its outcome checks into a verdict."""
from __future__ import annotations

from dataclasses import dataclass

from .checks import ERROR, FAIL, CheckResult
from .runner import RunRecord

OK = "OK"
SILENT_FAILURE = "SILENT_FAILURE"      # reported success, wrong outcome
REPORTED_FAILURE = "REPORTED_FAILURE"  # the job itself said it failed (non-zero exit, timeout, could not start)
UNVERIFIED = "UNVERIFIED"              # reported success, but the outcome could not be checked

EXIT_CODES = {OK: 0, SILENT_FAILURE: 1, REPORTED_FAILURE: 2, UNVERIFIED: 3}
LABELS = {
    OK: "Healthy: reported success and the outcome checks passed",
    SILENT_FAILURE: "Reported success, wrong outcome",
    REPORTED_FAILURE: "Job reported failure",
    UNVERIFIED: "Reported success, outcome could not be verified",
}


@dataclass
class Verdict:
    code: str
    reason: str

    @property
    def alert(self) -> bool:
        return self.code != OK

    @property
    def label(self) -> str:
        return LABELS[self.code]

    @property
    def exit_code(self) -> int:
        return EXIT_CODES[self.code]


def classify(run: RunRecord, results: list[CheckResult]) -> Verdict:
    failed = [r for r in results if r.status == FAIL]
    errored = [r for r in results if r.status == ERROR]
    if run.launch_error:
        return Verdict(REPORTED_FAILURE, f"command could not start: {run.launch_error}")
    if run.timed_out:
        return Verdict(REPORTED_FAILURE, "command timed out")
    if run.exit_code != 0:
        return Verdict(REPORTED_FAILURE, f"command exited with code {run.exit_code}")
    if failed:
        names = ", ".join(r.name for r in failed)
        return Verdict(SILENT_FAILURE, f"exit code 0, but {len(failed)} outcome check(s) failed: {names}")
    if errored:
        names = ", ".join(r.name for r in errored)
        return Verdict(UNVERIFIED, f"exit code 0, but {len(errored)} check(s) could not be evaluated: {names}")
    if not results:
        return Verdict(UNVERIFIED, "no outcome checks were run")
    return Verdict(OK, f"exit code 0 and all {len(results)} outcome check(s) passed")
