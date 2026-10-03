#!/usr/bin/env python3
"""Break billcall's own rules on purpose, in a copy, and watch the tests redden.

Each mutation copies the plugin folder to a temporary place, changes one exact text in one file of the
copy (the text must be there exactly once, or the mutation itself is reported as broken), runs the named
tests in the copy, and expects them red. The control mutation changes a comment and expects green. After
every run the sha256 of every original file is compared with the one taken at the start: the plugin itself
is never touched. The last line counts the mutations that misbehaved; anything but 0 is a failure.

  python3 tools/mutate_code.py            (about a minute; needs pytest, like the tests themselves)

tools/mutations.py does the same for the price table (data mutations); this file is for the code.
"""
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = "skills/billcall/scripts/billcall.py"
TESTS = "tests/test_billcall.py"
SKIP = (".git", "__pycache__", ".pytest_cache", "dist")

# (what is broken, file, exact text, replacement, tests to run, expected outcome)
MUTATIONS = (
    ("control: a comment reworded", SCRIPT,
     "# a personal plan: one per person", "# a personal plan: one for each person",
     TESTS + "::Rules", "green"),
    ("a personal plan for a company of several people passes", SCRIPT,
     'if row.get("use") == "personal" and company_people > 1:', "if False:",
     TESTS + "::Rules", "red"),
    ("crew: scripts may run on any plan, a coding plan or a seat included", SCRIPT,
     'if row.get("use") != "automated":', "if False:",
     TESTS + "::Cli", "red"),
    ("a plan's seat minimum is not checked", SCRIPT,
     "if lows and people < max(lows):", "if False:",
     TESTS + "::Rules", "red"),
    ("one API response on several log lines is counted several times", SCRIPT,
     "if key in seen:", "if False:",
     TESTS + "::Logs", "red"),
    ("the company policy's denied vendor passes", SCRIPT,
     "if any(name in vendor for name in denied):", "if False:",
     TESTS + "::Rules", "red"),
    ("modelPricing is offered for user settings, where Claude Code ignores it", SCRIPT,
     'if args.target != "managed":', "if False:",
     TESTS + "::Contract", "red"),
    ("a cheaper, weaker model is shown as a plain saving", SCRIPT,
     'word = "lower" if item["value"] < was["value"]', 'word = "lower" if item["value"] > was["value"]',
     TESTS + "::QualityAndSurfaces", "red"),
    ("people working in Cowork on a Standard seat are not told the pool runs out first", SCRIPT,
     'if surface not in AGENT_SURFACES or not people:', "if True:",
     TESTS + "::QualityAndSurfaces", "red"),
    ("the budget alarm forgets its 50% mark", SCRIPT,
     "LEVELS = (100, 80, 50)", "LEVELS = (100, 80)",
     TESTS + "::Guard", "red"),
)


def digest(root):
    """sha256 of every file under root (junk folders left out), keyed by relative path."""
    out = {}
    for folder, dirs, names in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d not in SKIP)
        for name in names:
            path = os.path.join(folder, name)
            with open(path, "rb") as handle:
                out[os.path.relpath(path, root)] = hashlib.sha256(handle.read()).hexdigest()
    return out


def run_one(tmp, n, mutation):
    """-> ("red" | "green" | "error", detail)."""
    _name, rel, old, new, tests, _expect = mutation
    copy = os.path.join(tmp, "m%02d" % n)
    shutil.copytree(ROOT, copy, ignore=shutil.ignore_patterns(*SKIP))
    target = os.path.join(copy, rel)
    with open(target, encoding="utf-8") as handle:
        text = handle.read()
    if text.count(old) != 1:
        return "error", "%r is in %s %d times, not once" % (old, rel, text.count(old))
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(text.replace(old, new, 1))
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")
    try:
        done = subprocess.run([sys.executable, "-m", "pytest", "-q", "-x", "-p", "no:cacheprovider", tests],
                              cwd=copy, env=env, capture_output=True, text=True, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return "error", repr(exc)
    last = (done.stdout.strip().splitlines() or [""])[-1]
    if done.returncode == 0:
        return "green", last
    if done.returncode == 1:
        return "red", last
    return "error", "pytest exit %d: %s" % (done.returncode, (done.stdout + done.stderr).strip()[-300:])


def main():
    before = digest(ROOT)
    bad = 0
    tmp = tempfile.mkdtemp(prefix="billcall-mutate-")
    try:
        for n, mutation in enumerate(MUTATIONS):
            name, expect = mutation[0], mutation[5]
            outcome, detail = run_one(tmp, n, mutation)
            if outcome == expect:
                print("ok   %s: %s as expected (%s)" % (name, expect, detail))
            else:
                print("BAD  %s: expected %s, got %s (%s)" % (name, expect, outcome, detail))
                bad += 1
            after = digest(ROOT)
            if after != before:
                print("BAD  the plugin's own files changed during '%s'" % name)
                bad += 1
                before = after
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("misbehaving: %d" % bad)
    return 0 if bad == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
