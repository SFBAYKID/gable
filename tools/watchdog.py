"""A standing critic that costs nothing to run.

Chase asked for feedback on the other agent's work every twenty minutes without
spending model credits. Judgment needs a model call, so this does the next best
thing: the critique is **encoded** rather than generated. Every check below is a
finding the reviewing agent already made and wrote down in `GABLE_HANDOFF.md`,
turned into an assertion a shell loop can evaluate.

That covers more than it sounds like. The failure modes here are known and
enumerable — a specific set of bugs, a specific set of rules that must not be
broken, and a specific definition of done. What a live reviewer would add is
novelty, not coverage.

Writes `~/Desktop/GABLE_FEEDBACK.md` every cycle. Overwrites rather than
appends, so the file is always the current state and never has to be scrolled.

Run it detached:

    nohup python3 tools/watchdog.py --interval 1200 >/dev/null 2>&1 &

Does not handle: anything requiring judgment about whether a flyer *looks*
right. It can tell you a template was marked certified with no evidence; it
cannot tell you the headline is two points too small.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Final

REPO: Final[Path] = Path(__file__).resolve().parent.parent
REPORT: Final[Path] = Path.home() / "Desktop" / "GABLE_FEEDBACK.md"

#: Severity ordering, worst first, so the report leads with what matters.
BLOCKER: Final[str] = "BLOCKER"
HIGH: Final[str] = "HIGH"
MEDIUM: Final[str] = "MEDIUM"
NOTE: Final[str] = "NOTE"
GOOD: Final[str] = "GOOD"
_ORDER: Final[dict[str, int]] = {BLOCKER: 0, HIGH: 1, MEDIUM: 2, NOTE: 3, GOOD: 4}


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing worth saying, in the voice the handoff uses."""

    severity: str
    title: str
    detail: str


def _run(command: list[str], timeout: int = 600) -> tuple[int, str]:
    """Run a command in the repo and capture combined output.

    Args:
        command: argv.
        timeout: Seconds before giving up.

    Returns:
        (exit code, output). A timeout returns (124, ...) like `timeout(1)`, and
        a missing binary returns (127, ...). The loop must never die because a
        check did.

    Raises:
        Nothing.
    """
    try:
        done = subprocess.run(
            command, cwd=REPO, capture_output=True, text=True, timeout=timeout, check=False
        )
        return done.returncode, (done.stdout + done.stderr)
    except subprocess.TimeoutExpired:
        return 124, f"timed out after {timeout}s"
    except FileNotFoundError:
        return 127, f"not found: {command[0]}"
    except Exception as exc:  # pragma: no cover - defensive
        return 1, str(exc)


def _read(relative: str) -> str:
    """File contents, or empty string if it is not there."""
    try:
        return (REPO / relative).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def _venv_python() -> str:
    """The project interpreter if it exists, else whatever is running this."""
    candidate = REPO / ".venv" / "bin" / "python"
    return str(candidate) if candidate.exists() else sys.executable


# --- the gates --------------------------------------------------------------


def check_gates() -> list[Finding]:
    """Run ruff, mypy and pytest. A red tree blocks everything else."""
    found: list[Finding] = []
    python = _venv_python()

    code, out = _run([python, "-m", "ruff", "check", "."])
    if code == 0:
        found.append(Finding(GOOD, "ruff passes", ""))
    else:
        found.append(Finding(HIGH, "ruff fails", _tail(out, 12)))

    code, out = _run([python, "-m", "mypy"])
    if code == 0:
        found.append(Finding(GOOD, "mypy passes", ""))
    else:
        found.append(Finding(HIGH, "mypy fails", _tail(out, 12)))

    code, out = _run([python, "-m", "pytest", "-q", "--no-header", "-x", "-q"], timeout=900)
    if code == 0:
        found.append(Finding(GOOD, "pytest passes", _tail(out, 3)))
    else:
        failed = [ln for ln in out.splitlines() if ln.startswith("FAILED")]
        found.append(
            Finding(
                BLOCKER,
                "pytest is red — nothing else should proceed",
                "\n".join(failed[:15]) or _tail(out, 15),
            )
        )
    return found


def _tail(text: str, lines: int) -> str:
    """Last N non-empty lines, for a report that stays readable."""
    kept = [ln for ln in text.splitlines() if ln.strip()]
    return "\n".join(kept[-lines:])


# --- the known bugs, as regressions -----------------------------------------

#: (severity, title, path, pattern, still_present_means, handoff reference).
#: A match means the bug is STILL THERE. These are the findings from
#: GABLE_HANDOFF.md §6, mechanised.
_BUG_PATTERNS: Final[tuple[tuple[str, str, str, str, str, str], ...]] = (
    (
        BLOCKER,
        "The re-poll loop is still open — this is the money leak",
        "MARKER:repoll-loop",
        "",
        "needs_photo / needs_review / needs_info are still non-terminal, so the "
        "poller hands the same row back every two minutes forever — new Firecrawl "
        "call, new Drive copy, new paid vision call, same question re-posted.",
        "6.1",
    ),
    (
        HIGH,
        "Research still repeats on every pass",
        "src/gable/listings/intake.py",
        r'PUBLIC_FACTS[^=]*=\s*\([^)]*"price"',
        'PUBLIC_FACTS still says "price" while Facts.as_dict() emits "list_price", '
        "so the cache can never satisfy it and every New Listing is researched again "
        "on every poll. One word to fix.",
        "6.6",
    ),
    (
        HIGH,
        "The spend ceiling is still a comment",
        "MARKER:spend-unimported",
        "",
        "src/gable/spend.py is imported by nothing and record_spend has no writers. "
        "AGENTS.md §7 requires the ceiling be in code, not a comment — it currently "
        "is one.",
        "§4.3",
    ),
    (
        HIGH,
        "place_photo still reports success unconditionally",
        "src/gable/pipeline/live.py",
        r"return True\s*$",
        "The `if not placed:` branch in runner.py can never fire in production, so a "
        "real placement failure surfaces as the generic 'something went wrong'.",
        "6.16",
    ),
    (
        MEDIUM,
        "The hero object id is still hardcoded",
        "src/gable/pipeline/live.py",
        r'"gableHero"',
        "A second call on the same presentation is a 400 duplicate-object-id, which "
        "is reachable through the re-poll loop.",
        "6.15",
    ),
    (
        HIGH,
        "Gable still announces edits it cannot perform",
        "src/gable/slackapp/app.py",
        r'return f"On it — \{readable\} now\."',
        "describe_action promises set_font_size / set_colour / resize_photo and no "
        "handler executes them. Directly violates AGENTS.md §1. This is also the "
        "'ask for any edit and it can do it' requirement.",
        "6.10",
    ),
    (
        BLOCKER,
        "The database still cannot be shared across threads",
        "src/gable/db/schema.py",
        r"sqlite3\.connect\((?![^)]*check_same_thread)",
        "Socket Mode blocks forever, so the poller must run on another thread — and "
        "sqlite3 raises ProgrammingError on cross-thread use. The entry point cannot "
        "work until this is resolved.",
        "5.1",
    ),
    (
        MEDIUM,
        "Text readers still do not recurse into groups",
        "MARKER:group-recursion",
        "",
        "read_slide_text and read_text_boxes iterate pageElements and read "
        "element['shape']. A PPTX-imported deck wraps content in elementGroup, which "
        "has no shape key — so literals inside groups are never seen, never replaced, "
        "and survive to the flyer. Expect this to be the root cause of a cluster of "
        "template certification failures.",
        "6.5",
    ),
    (
        MEDIUM,
        "The second quality pass is still dead",
        "src/gable/pipeline/runner.py",
        r"judge\([^)]*,\s*1\)",
        "QUALITY_PASSES = 2 is declared and unused; one vision call runs. Chase asked "
        "for two because fixing the first can move something, and the fitting pass "
        "changes the slide before the only inspection.",
        "6.11",
    ),
    (
        MEDIUM,
        "Substring replacement is still unguarded",
        "MARKER:substring-safety",
        "",
        "MIN_FIND_LENGTH exists to stop 'Phone' matching inside 'Phone Number'. "
        "Nothing checks whether a literal changed more than once.",
        "6.12",
    ),
    (
        MEDIUM,
        "The database path is still not configuration",
        "MARKER:db-path-config",
        "",
        "db_path is not a field on config.Settings, and .env.example points at "
        "/opt/gable/gable.db which is read-only under ProtectSystem=strict "
        "(ReadWritePaths is /opt/gable/var only). WAL also needs to create -wal and "
        "-shm beside it.",
        "6.14",
    ),
)


def check_known_bugs() -> list[Finding]:
    """Flag any handoff finding that is still present in the source."""
    found: list[Finding] = []
    for severity, title, path, pattern, detail, ref in _BUG_PATTERNS:
        still_there = False

        if path == "MARKER:repoll-loop":
            store = _read("src/gable/db/store.py")
            # Fixed either by suppressing paused runs from polling, or by a
            # bounded attempt ceiling. Either alone closes the loop.
            still_there = "PAUSED" not in store and "MAX_RUN_ATTEMPTS" not in store
        elif path == "MARKER:spend-unimported":
            hits = _grep_repo(r"from gable\.spend|import spend\b|record_spend\(")
            still_there = (
                len(
                    [
                        h
                        for h in hits
                        if "/spend.py" not in h and "store.py" not in h and "watchdog.py" not in h
                    ]
                )
                == 0
            )
        elif path == "MARKER:group-recursion":
            live = _read("src/gable/pipeline/live.py")
            still_there = "elementGroup" not in live and "children" not in live
        elif path == "MARKER:db-path-config":
            still_there = "db_path" not in _read("src/gable/config.py")
        elif path == "MARKER:substring-safety":
            live = _read("src/gable/pipeline/live.py")
            still_there = "safe_replacement_requests" not in live or "occurrences != 1" not in live
        else:
            body = _read(path)
            still_there = bool(body) and bool(re.search(pattern, body, re.MULTILINE))

        if still_there:
            found.append(Finding(severity, f"{title}  (handoff {ref})", detail))
    return found


def _grep_repo(pattern: str) -> list[str]:
    """Lines in src/ and tools/ matching a regex."""
    code, out = _run(["grep", "-rEn", "--exclude-dir=__pycache__", pattern, "src", "tools"])
    return out.splitlines() if code == 0 else []


# --- the rules that must not be broken --------------------------------------


def check_safety() -> list[Finding]:
    """The things that are worse than not finishing."""
    found: list[Finding] = []

    code, out = _run(["git", "log", "--all", "--name-only", "--pretty=format:", "-20"])
    committed = {ln.strip() for ln in out.splitlines() if ln.strip()}
    leaked = [
        f
        for f in committed
        if f == ".env" or f.endswith("-key.json") or ("service" in f and f.endswith(".json"))
    ]
    if leaked:
        found.append(
            Finding(
                BLOCKER,
                "A credential file appears in git history",
                "\n".join(sorted(leaked)[:5])
                + "\nRotate the credential and tell Chase immediately.",
            )
        )

    ignored = _read(".gitignore")
    if ".env" not in ignored:
        found.append(
            Finding(
                BLOCKER, ".env is not in .gitignore", "CLAUDE.md §3 requires this from commit #1."
            )
        )

    # The refusals are the product. If they vanish, something was "fixed" the
    # wrong way — most likely to make a test pass.
    runner = _read("src/gable/pipeline/runner.py")
    if runner and "needs_photo" not in runner:
        found.append(
            Finding(
                BLOCKER,
                "The no-photo refusal is gone from the runner",
                "A flyer with sky-and-grass where the house goes is worse than no flyer. "
                "If a test failed because this fired, the fixture was wrong, not the refusal.",
            )
        )
    poller = _read("src/gable/pipeline/poller.py")
    if poller and "ready" not in poller:
        found.append(
            Finding(
                BLOCKER,
                "The backfill guard is gone",
                "99 historical rows become 99 flyers without it.",
            )
        )

    # Sheet writes outside the Runs tab.
    for line in _grep_repo(r"\.update\(|values\(\)\.update|batchUpdate\(.*Form Responses"):
        if "sheets" in line and "Runs" not in line:
            found.append(
                Finding(HIGH, "Possible write to the Sheet outside the Runs tab", line[:200])
            )
            break

    # Emoji anywhere a reader could see one.
    code, out = _run(
        [
            "grep",
            "-rEn",
            "--exclude-dir=__pycache__",
            "[\U0001f300-\U0001faff☀-➿]",
            "src",
            "AGENTS.md",
            "ARCHITECTURE.md",
        ]
    )
    if code == 0 and out.strip():
        found.append(
            Finding(
                MEDIUM,
                "Emoji present — the house style forbids them",
                _tail(out, 5),
            )
        )

    # File size ceiling.
    oversized: list[str] = []
    for path in sorted(REPO.glob("src/gable/**/*.py")):
        try:
            count = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
        except OSError:
            continue
        if count > 800:
            oversized.append(f"{path.relative_to(REPO)}: {count} lines")
    if oversized:
        found.append(Finding(HIGH, "A file crossed the 800-line ceiling", "\n".join(oversized)))

    return found


# --- progress ---------------------------------------------------------------


def check_progress(stall_minutes: int) -> list[Finding]:
    """Is the other agent actually moving, and is it telling the truth?"""
    found: list[Finding] = []

    _, out = _run(["git", "log", "-8", "--pretty=format:%h %cr  %s"])
    if out.strip():
        found.append(Finding(NOTE, "Recent commits", out.strip()))

    code, out = _run(["git", "log", "-1", "--pretty=format:%ct"])
    if code == 0 and out.strip().isdigit():
        age_min = (time.time() - int(out.strip())) / 60
        if age_min > stall_minutes:
            found.append(
                Finding(
                    HIGH,
                    f"No commit in {int(age_min)} minutes",
                    "Either a long piece of work is in flight, or the agent is stuck. "
                    "If it is stuck, CLAUDE.md §12 asks for a plain blocker report rather "
                    "than a workaround built on a guess.",
                )
            )

    _, out = _run(["git", "status", "--short"])
    if out.strip():
        found.append(Finding(NOTE, "Uncommitted work in the tree", _tail(out, 20)))

    # Certification ledger honesty.
    ledger = _read("TEMPLATE_CERTIFICATION.md")
    if not ledger:
        found.append(
            Finding(
                MEDIUM,
                "No certification ledger yet",
                "Tier B needs TEMPLATE_CERTIFICATION.md, updated per template as you go — "
                "not batched at the end. Without it a crash loses the whole loop.",
            )
        )
    else:
        certified = len(re.findall(r"\bcertified\b", ledger, re.IGNORECASE))
        failed = len(re.findall(r"\bfailed\b", ledger, re.IGNORECASE))
        found.append(
            Finding(NOTE, "Certification ledger", f"{certified} certified, {failed} failed, of 45")
        )
        if certified and not (REPO / "tools" / "simulate_workflow.py").exists():
            found.append(
                Finding(
                    HIGH,
                    "Templates marked certified with no simulation harness present",
                    "tools/simulate_workflow.py does not exist, so nothing ran the full "
                    "workflow for those templates. 'It rendered without an exception' is not "
                    "certification — the bar is visual.",
                )
            )

    if (
        not (REPO / "src" / "gable" / "cli.py")
        .read_text(encoding="utf-8", errors="replace")
        .count("def main")
    ):
        found.append(
            Finding(
                HIGH,
                "Still no entry point",
                "cli.py has no main(). Nothing constructs a Poller, calls build_runner, or "
                "calls Runner.run. Until this exists a form submission produces silence.",
            )
        )

    if not _grep_repo(r"file_share|url_private|files_info"):
        found.append(
            Finding(
                HIGH,
                "The Slack photo handoff still does not exist",
                "Nothing receives a file_share event or downloads url_private. This is the "
                "largest missing piece and the demo cannot run without it. Note app.py's "
                "message handler returns early on any subtype, which drops file shares.",
            )
        )

    return found


# --- the report -------------------------------------------------------------


def render(findings: list[Finding], cycle: int) -> str:
    """Build the Desktop report."""
    findings.sort(key=lambda f: _ORDER.get(f.severity, 9))
    stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    blockers = sum(1 for f in findings if f.severity == BLOCKER)
    highs = sum(1 for f in findings if f.severity == HIGH)

    if blockers:
        verdict = f"**{blockers} blocker(s) open.** Fix these before anything else."
    elif highs:
        verdict = f"**No blockers. {highs} high-priority item(s) open.**"
    else:
        verdict = "**Clear.** No blockers or high-priority items detected."

    out = [
        "# Gable — automated review",
        "",
        f"Generated {stamp} (cycle {cycle}). Regenerated every cycle; always current.",
        "",
        verdict,
        "",
        "These checks are the findings from `GABLE_HANDOFF.md` turned into assertions.",
        "A finding here means the condition is **still present in the source right now**.",
        "This cannot judge whether a flyer *looks* right — that stays human.",
        "",
        "---",
        "",
    ]
    for severity in (BLOCKER, HIGH, MEDIUM, NOTE, GOOD):
        group = [f for f in findings if f.severity == severity]
        if not group:
            continue
        out.append(f"## {severity}")
        out.append("")
        for finding in group:
            out.append(f"### {finding.title}")
            if finding.detail:
                out.append("")
                out.append("```")
                out.append(finding.detail.strip()[:1800])
                out.append("```")
            out.append("")
    out.append("---")
    out.append("")
    out.append("Order of work is in `GABLE_HANDOFF.md`. The guardrails there still apply:")
    out.append("never leave the poller running unattended, never enter a credential,")
    out.append("never mutate the Sheet's form responses, never code around a template")
    out.append("defect, and never commit red.")
    return "\n".join(out)


def one_cycle(cycle: int, stall_minutes: int) -> int:
    """Run every check once and write the report.

    Returns:
        How many blockers were found.
    """
    findings: list[Finding] = []
    for check in (check_gates, check_known_bugs, check_safety):
        try:
            findings.extend(check())
        except Exception as exc:  # pragma: no cover - the loop must survive
            findings.append(Finding(NOTE, f"A check failed to run: {check.__name__}", str(exc)))
    try:
        findings.extend(check_progress(stall_minutes))
    except Exception as exc:  # pragma: no cover
        findings.append(Finding(NOTE, "Progress check failed to run", str(exc)))

    try:
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(render(findings, cycle), encoding="utf-8")
    except OSError as exc:  # pragma: no cover
        print(f"could not write report: {exc}", file=sys.stderr)
    return sum(1 for f in findings if f.severity == BLOCKER)


def main() -> int:
    """Loop until killed."""
    parser = argparse.ArgumentParser(description="Standing critic for the Gable build.")
    parser.add_argument("--interval", type=int, default=1200, help="Seconds between cycles.")
    parser.add_argument(
        "--stall-minutes", type=int, default=45, help="Flag if no commit in this long."
    )
    parser.add_argument("--once", action="store_true", help="Run one cycle and exit.")
    args = parser.parse_args()

    cycle = 1
    while True:
        blockers = one_cycle(cycle, args.stall_minutes)
        print(f"cycle {cycle}: {blockers} blocker(s) -> {REPORT}")
        if args.once:
            return 0
        cycle += 1
        time.sleep(max(60, args.interval))


if __name__ == "__main__":
    sys.exit(main())
