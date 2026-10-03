#!/usr/bin/env python3
"""Check that billcall's facts and idea maps leave nothing out.

  python3 tools/check_coverage.py                     against the snapshot in data/coverage.json
  python3 tools/check_coverage.py --plan PLAN.md      and every tagged line of the plan itself
  python3 tools/check_coverage.py --run-dir RUN       and every late opinion in RUN/opinions-late

data/coverage.json names what must be covered: the plan lines that carry a source tag or an open
question, the opinion files, the ranges of the JEV notes. Each needs at least one row in its
Markdown table with a status or verdict from the closed lists below.

Exit code 0: nothing missing. 1: something missing. 2: bad input.
"""
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COVERAGE = os.path.join(ROOT, "data", "coverage.json")

FACT_STATUSES = ("confirmed", "confirmed-secondary", "refuted", "refuted-secondary", "not-found")
FACT_SOURCES = ("https://", "repo:", "run:")
MAP_VERDICTS = ("accepted", "rejected", "merged", "partly")
def _word(*codes):
    """A word from its code points: this file stays ASCII, so billcall's language check stays green."""
    return "".join(chr(code) for code in codes)


# The plan's three source tags, built from code points so this file stays ASCII (until 2026-10-02 they
# were typed as \\u escapes, which the writing tool turned into Cyrillic letters, and the language check
# of the plugin went red): "critic checked it", "late opinion", "secondary source only".
PLAN_TAGS = ("[" + _word(0x43A, 0x440, 0x438, 0x442, 0x438, 0x43A) + "]",
             "[" + _word(0x43F, 0x43E, 0x437, 0x434, 0x43D) + ".]",
             "[" + _word(0x432, 0x442, 0x43E, 0x440, 0x438, 0x447, 0x43D) + ".]")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
PLAN_REF_RE = re.compile(r"^plan\.md:(\d+)$")
USAGE = "usage: check_coverage.py [--coverage FILE] [--root DIR] [--plan PLAN.md] [--run-dir RUN]"


def is_whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def read_text(path):
    with open(path, encoding="utf-8") as handle:
        return handle.read()


def table_rows(text, prefix):
    """(line number, cells) of every Markdown table row whose first cell is prefix + digits."""
    pattern = re.compile(r"^\|\s*" + re.escape(prefix) + r"\d+\s*\|")
    rows = []
    for number, line in enumerate(text.splitlines(), 1):
        if pattern.match(line):
            body = line.strip()
            if body.endswith("|"):
                body = body[:-1]
            rows.append((number, [cell.strip() for cell in body[1:].split("|")]))
    return rows


def split_refs(cell):
    """'plan.md:45, plan.md:138' -> ['plan.md:45', 'plan.md:138'] (backticks dropped)."""
    return [part.strip().strip("`").strip() for part in cell.split(",") if part.strip()]


def check_facts(text, required):
    """Facts table: id | refs | claim | status | source | checked | note."""
    rows = table_rows(text, "F")
    if not rows:
        return ["facts: no rows (a row starts with '| F01 |')"]
    out = []
    covered = set()
    seen = set()
    for number, cells in rows:
        where = "facts line %d" % number
        if len(cells) != 7:
            out.append("%s: expected 7 cells (id, refs, claim, status, source, checked, note), got %d"
                       % (where, len(cells)))
            continue
        fid, refs, claim, status, source, checked = cells[:6]
        if fid in seen:
            out.append("%s: duplicate id %s" % (where, fid))
        seen.add(fid)
        if status not in FACT_STATUSES:
            out.append("%s: status %r is not one of: %s" % (where, status, ", ".join(FACT_STATUSES)))
            continue
        if not claim:
            out.append("%s: the claim is empty" % where)
        if status == "not-found":
            if not source.lower().startswith("where searched:"):
                out.append("%s: a not-found row says 'where searched: ...' in its source cell" % where)
        elif not any(mark in source for mark in FACT_SOURCES):
            out.append("%s: a %s row needs a source (https://, repo: or run:)" % (where, status))
        if not DATE_RE.match(checked):
            out.append("%s: checked must be a date YYYY-MM-DD" % where)
        covered.update(split_refs(refs))
    for ref in required:
        if ref not in covered:
            out.append("facts: %s has no row with a status" % ref)
    return out


def check_map(text, prefix, columns, required, whole_ref, name):
    """A map table: id | source | idea | verdict | where it went [| plan ref]."""
    rows = table_rows(text, prefix)
    if not rows:
        return ["%s: no rows (a row starts with '| %s01 |')" % (name, prefix)]
    out = []
    covered = set()
    seen = set()
    for number, cells in rows:
        where = "%s line %d" % (name, number)
        if len(cells) != columns:
            out.append("%s: expected %d cells, got %d" % (where, columns, len(cells)))
            continue
        if cells[0] in seen:
            out.append("%s: duplicate id %s" % (where, cells[0]))
        seen.add(cells[0])
        if cells[3] not in MAP_VERDICTS:
            out.append("%s: verdict %r is not one of: %s" % (where, cells[3], ", ".join(MAP_VERDICTS)))
            continue
        if not cells[2]:
            out.append("%s: the idea is empty" % where)
        if not cells[4]:
            out.append("%s: says nowhere where the idea went" % where)
        for ref in split_refs(cells[1]):
            covered.add(ref if whole_ref else ref.split(":")[0])
    for item in required:
        if item not in covered:
            out.append("%s: %s has no row with a verdict" % (name, item))
    return out


def check_all(root, coverage):
    """The facts file and both maps against the snapshot. -> list of problems."""
    facts = coverage.get("facts") or {}
    late = coverage.get("late_map") or {}
    jev = coverage.get("jev_map") or {}
    required_refs = list(facts.get("tagged_refs", [])) + list(facts.get("other_refs", []))
    parts = (
        ("facts", facts.get("file"), lambda text: check_facts(text, required_refs)),
        ("late map", late.get("file"),
         lambda text: check_map(text, "L", 5, late.get("required", []), False, "late map")),
        ("JEV map", jev.get("file"),
         lambda text: check_map(text, "J", 6, jev.get("required", []), True, "JEV map")),
    )
    out = []
    for name, rel, check in parts:
        if not isinstance(rel, str) or not rel:
            out.append("%s: data/coverage.json names no file" % name)
            continue
        try:
            text = read_text(os.path.join(root, rel))
        except (OSError, ValueError) as exc:
            out.append("%s: cannot read %s: %s" % (name, rel, exc))
            continue
        out.extend(check(text))
    return out


def plan_tag_lines(text):
    """{line number: [tags]} for every plan line that carries a source tag."""
    found = {}
    for number, line in enumerate(text.splitlines(), 1):
        tags = [tag for tag in PLAN_TAGS if tag in line]
        if tags:
            found[number] = tags
    return found


def check_plan(text, coverage):
    """The snapshot's tagged lines are exactly the plan's tagged lines, or explained as not claims."""
    facts = coverage.get("facts") or {}
    tagged_refs = set(facts.get("tagged_refs", []))
    not_claims = facts.get("plan_lines_not_claims") or {}
    tagged = plan_tag_lines(text)
    out = []
    for number in sorted(tagged):
        if "plan.md:%d" % number not in tagged_refs and str(number) not in not_claims:
            out.append("plan: line %d carries a tag that data/coverage.json does not list - add plan.md:%d "
                       "to facts.tagged_refs with a facts row, or explain it in plan_lines_not_claims"
                       % (number, number))
    for ref in sorted(tagged_refs):
        match = PLAN_REF_RE.match(ref)
        if not match:
            out.append("plan: %s is not of the form plan.md:N" % ref)
        elif int(match.group(1)) not in tagged:
            out.append("plan: %s carries no tag any more - the plan moved; re-derive the refs" % ref)
    for key in sorted(not_claims):
        if not key.isdigit() or int(key) not in tagged:
            out.append("plan: plan_lines_not_claims names line %s, which carries no tag" % key)
    lines = len(text.splitlines())
    expected = coverage.get("plan_lines")
    if is_whole(expected) and expected != lines:
        out.append("plan: the snapshot was made from a plan of %d lines, this one has %d - re-check other_refs"
                   % (expected, lines))
    return out


def check_run_dir(run_dir, coverage):
    """Every file in RUN/opinions-late is in the late-opinions map, and every mapped one still exists."""
    late_dir = os.path.join(run_dir, "opinions-late")
    try:
        names = sorted(name for name in os.listdir(late_dir) if name.endswith(".md"))
    except OSError as exc:
        return ["run dir: cannot list %s: %s" % (late_dir, exc)]
    required = set((coverage.get("late_map") or {}).get("required", []))
    out = []
    for name in names:
        if "opinions-late/" + name not in required:
            out.append("run dir: opinions-late/%s is not in the late-opinions map - read it and add a row" % name)
    for item in sorted(required):
        if item.startswith("opinions-late/") and item[len("opinions-late/"):] not in names:
            out.append("run dir: %s is in the map but not in the folder" % item)
    return out


def load_coverage(path=COVERAGE):
    """-> (coverage, None) or (None, the reason it cannot be used)."""
    try:
        coverage = json.loads(read_text(path))
    except (OSError, ValueError) as exc:
        return None, "cannot read %s: %s" % (path, exc)
    if not isinstance(coverage, dict):
        return None, "%s must hold one JSON object" % path
    for section, keys in (("facts", ("tagged_refs", "other_refs")), ("late_map", ("required",)),
                          ("jev_map", ("required",))):
        part = coverage.get(section)
        if not isinstance(part, dict) or not isinstance(part.get("file"), str):
            return None, "%s: %s needs an object with a file" % (path, section)
        for key in keys:
            value = part.get(key)
            if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
                return None, "%s: %s.%s must be a list of strings" % (path, section, key)
    if not isinstance(coverage["facts"].get("plan_lines_not_claims", {}), dict):
        return None, "%s: facts.plan_lines_not_claims must be an object" % path
    return coverage, None


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    options = {"--coverage": COVERAGE, "--root": ROOT, "--plan": None, "--run-dir": None}
    i = 0
    while i < len(args):
        if args[i] in options and i + 1 < len(args):
            options[args[i]] = args[i + 1]
            i += 2
        else:
            print(USAGE)
            return 2
    coverage, error = load_coverage(options["--coverage"])
    if error:
        print("check_coverage: " + error)
        return 2
    plan_text = None
    if options["--plan"]:
        try:
            plan_text = read_text(options["--plan"])
        except (OSError, ValueError) as exc:
            print("check_coverage: cannot read %s: %s" % (options["--plan"], exc))
            return 2
    try:
        problems = check_all(options["--root"], coverage)
        if plan_text is not None:
            problems.extend(check_plan(plan_text, coverage))
        if options["--run-dir"]:
            problems.extend(check_run_dir(options["--run-dir"], coverage))
    except Exception as exc:  # a bug in this checker must redden the run, never crash it
        problems = ["the checker itself failed: %r" % (exc,)]
    for problem in problems:
        print("BAD: " + problem)
    facts = coverage["facts"]
    print("check_coverage: %d plan ref(s), %d opinion file(s), %d JEV range(s), %d problem(s)" % (
        len(facts["tagged_refs"]) + len(facts["other_refs"]), len(coverage["late_map"]["required"]),
        len(coverage["jev_map"]["required"]), len(problems)))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
