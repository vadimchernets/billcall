#!/usr/bin/env python3
"""Break the guarded things on purpose and watch the checks redden.

Each mutation edits a copy in a temporary folder, runs the check on that copy, then confirms that
the sha256 of every original file did not move. The baseline and the controls change nothing that
matters and must stay green. The last line counts the mutations that misbehaved: anything but 0
is a failure.

  python3 tools/mutations.py
"""
import copy
import hashlib
import json
import os
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import check_coverage  # noqa: E402
import check_prices  # noqa: E402

PRICES = os.path.join(ROOT, "data", "prices.json")


def sha256(path):
    with open(path, "rb") as handle:
        return hashlib.sha256(handle.read()).hexdigest()


def first(rows, test=None):
    for row in rows:
        if test is None or test(row):
            return row
    raise LookupError("no row fits this mutation")


# ---- price table mutations: each takes the parsed table and changes it in place ----

def drop_url(doc):
    del first(doc["rows"])["url"]


def status_maybe(doc):
    first(doc["rows"])["status"] = "maybe"


def drop_use(doc):
    del first(doc["rows"])["use"]


def checked_2025(doc):
    first(doc["rows"])["checked"] = "2025-01-01"


def valid_until_past(doc):
    first(doc["rows"])["valid_until"] = "2026-01-01"


def plain_http(doc):
    first(doc["rows"])["url"] = "http://claude.com/pricing"


def unknown_kind(doc):
    first(doc["rows"])["kind"] = "bundle"


def negative_amount(doc):
    first(doc["rows"])["prices"][0]["amount"] = -1


def duplicate_id(doc):
    doc["rows"].append(copy.deepcopy(doc["rows"][0]))


def api_without_quality(doc):
    del first(doc["rows"], lambda row: row.get("kind") == "api")["quality_index"]


def secondary_without_note(doc):
    # every row may be official; mark one secondary first, so the mutation needs no secondary row in the table
    row = first(doc["rows"], lambda row: row.get("status") == "secondary" or "note" in row)
    row["status"] = "secondary"
    del row["note"]


def coding_plan_for_scripts(doc):
    first(doc["rows"], lambda row: row.get("kind") == "coding-plan")["use"] = "automated"


def control_price(doc):
    for row in doc["rows"]:
        for price in row["prices"]:
            if price["amount"] == 20:
                price["amount"] = 21
                return
    raise LookupError("no price of 20 to change")


def nothing(doc):
    return None


PRICE_MUTATIONS = (
    ("baseline: the table as it is", nothing, "green"),
    ("url removed", drop_url, "red"),
    ("status 'maybe'", status_maybe, "red"),
    ("use removed", drop_use, "red"),
    ("checked 2025-01-01 (strict)", checked_2025, "red"),
    ("valid_until in the past", valid_until_past, "red"),
    ("url over plain http", plain_http, "red"),
    ("kind not in the list", unknown_kind, "red"),
    ("negative price", negative_amount, "red"),
    ("duplicate id", duplicate_id, "red"),
    ("api row without quality_index", api_without_quality, "red"),
    ("secondary row without a note", secondary_without_note, "red"),
    ("coding plan marked for scripts", coding_plan_for_scripts, "red"),
    ("control: a price of 20 becomes 21", control_price, "green"),
)


# ---- facts and map mutations: each takes the text of one file and returns the new text ----

def drop_rows(prefix, needle):
    def mutate(text):
        lines = text.splitlines()
        kept = [line for line in lines if not (line.startswith("| " + prefix) and needle in line)]
        if len(kept) == len(lines):
            raise LookupError("no %s row mentions %s" % (prefix, needle))
        return "\n".join(kept) + "\n"
    return mutate


def replace_once(old, new):
    def mutate(text):
        if old not in text:
            raise LookupError("%r is not in the file" % old)
        return text.replace(old, new, 1)
    return mutate


def reword_first_claim(text):
    lines = text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("| F"):
            cells = line.split("|")
            cells[3] = cells[3].rstrip() + " (control edit) "
            lines[i] = "|".join(cells)
            return "\n".join(lines) + "\n"
    raise LookupError("no facts row to reword")


def keep(text):
    return text


COVERAGE_MUTATIONS = (
    ("baseline: facts and maps as they are", "facts", keep, "green"),
    ("facts: the row of plan.md:218 removed", "facts", drop_rows("F", "plan.md:218"), "red"),
    ("facts: a status not in the list", "facts", replace_once("| confirmed |", "| maybe |"), "red"),
    ("late map: every row of opinions-late/meta-nb1.md removed", "late_map",
     drop_rows("L", "opinions-late/meta-nb1.md"), "red"),
    ("late map: a verdict not in the list", "late_map", replace_once("| accepted |", "| maybe |"), "red"),
    ("JEV map: the row of 92.txt:893 removed", "jev_map", drop_rows("J", "92.txt:893"), "red"),
    ("control: a facts claim reworded", "facts", reword_first_claim, "green"),
)


def run_price_mutation(doc, today, mutate, folder):
    mutated = copy.deepcopy(doc)
    mutate(mutated)
    path = os.path.join(folder, "prices.json")
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(mutated, handle)
    problems, error, _rows = check_prices.check_file(path, today=today, strict=True)
    return [error] if error else problems


def run_coverage_mutation(coverage, section, mutate, folder):
    for name in ("facts", "late_map", "jev_map"):
        rel = coverage[name]["file"]
        dest = os.path.join(folder, rel)
        if not os.path.isdir(os.path.dirname(dest)):
            os.makedirs(os.path.dirname(dest))
        shutil.copyfile(os.path.join(ROOT, rel), dest)
    target = os.path.join(folder, coverage[section]["file"])
    text = check_coverage.read_text(target)
    with open(target, "w", encoding="utf-8") as handle:
        handle.write(mutate(text))
    return check_coverage.check_all(folder, coverage)


def judge(name, expect, run):
    """Run one mutation; print one line; -> 1 when it misbehaved, else 0."""
    try:
        problems = run()
        outcome = "red" if problems else "green"
        detail = "%d problem(s)" % len(problems)
    except Exception as exc:  # a mutation that cannot be applied is a misbehaving mutation
        outcome = "error"
        detail = repr(exc)
    if outcome == expect:
        print("ok   %s: %s as expected (%s)" % (name, expect, detail))
        return 0
    print("BAD  %s: expected %s, got %s (%s)" % (name, expect, outcome, detail))
    return 1


def main(argv=None):
    coverage, error = check_coverage.load_coverage()
    doc, doc_error = check_prices.load(PRICES)
    if error or doc_error:
        print("mutations: " + (error or doc_error))
        print("misbehaving: 1")
        return 1
    today = check_prices.parse_date(doc.get("updated")) if isinstance(doc, dict) else None
    if today is None:
        print("mutations: data/prices.json has no 'updated' date to judge the table on")
        print("misbehaving: 1")
        return 1
    originals = [PRICES] + [os.path.join(ROOT, coverage[name]["file"]) for name in ("facts", "late_map", "jev_map")]
    before = dict((path, sha256(path)) for path in originals)
    bad = 0
    tmp = tempfile.mkdtemp(prefix="billcall-mutations-")
    try:
        runs = []
        for name, mutate, expect in PRICE_MUTATIONS:
            runs.append((name, expect, lambda mutate=mutate, n=len(runs): run_price_mutation(
                doc, today, mutate, make_folder(tmp, n))))
        for name, section, mutate, expect in COVERAGE_MUTATIONS:
            runs.append((name, expect, lambda section=section, mutate=mutate, n=len(runs): run_coverage_mutation(
                coverage, section, mutate, make_folder(tmp, n))))
        for name, expect, run in runs:
            bad += judge(name, expect, run)
            for path in originals:
                if sha256(path) != before[path]:
                    print("BAD  the original %s changed during '%s'" % (os.path.relpath(path, ROOT), name))
                    bad += 1
                    before[path] = sha256(path)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("misbehaving: %d" % bad)
    return 0 if bad == 0 else 1


def make_folder(tmp, n):
    folder = os.path.join(tmp, "m%02d" % n)
    os.makedirs(folder)
    return folder


if __name__ == "__main__":
    sys.exit(main())
