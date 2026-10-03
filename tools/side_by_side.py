#!/usr/bin/env python3
"""Write the three side-by-side tables into the facts files, from data/prices.json.

  python3 tools/side_by_side.py            write them (between the markers) into data/facts-2026-10.md
                                           and data/ru/facts-2026-10.md
  python3 tools/side_by_side.py --check    exit 1 when a file's tables differ from the price table

The tables - the US five with Microsoft and GitHub, the Chinese vendors, own machines and rent with the
models to run on them - and the cheap-class ratio under the Chinese one (how many times the cheap Chinese
models are cheaper than Claude, GPT-6 Luna and Gemini Flash, with the quality index on both sides; its headline
in the file's language) are never typed by hand: a price corrected in data/prices.json changes them, and
--check (run by the tests) reddens until they are written again. Each sits between
<!-- side-by-side:GROUP:start --> and <!-- side-by-side:GROUP:end -->.
"""
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "skills", "billcall", "scripts", "billcall.py")
FILES = ((os.path.join(ROOT, "data", "facts-2026-10.md"), "en"), (os.path.join(ROOT, "data", "ru", "facts-2026-10.md"), "ru"))
CHEAP = "china-cheap"


def load_billcall():
    spec = importlib.util.spec_from_file_location("billcall_for_tables", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def tables(billcall, table, lang="en"):
    out = {}
    for group in billcall.GROUPS:
        header, lines = billcall.side_by_side(table, group)
        out[group] = billcall.say_side_by_side(header, lines)
    out[CHEAP] = billcall.say_cheap_class(billcall.cheap_class(table), table, billcall.lang_words(lang))
    return out


def marks(group):
    return "<!-- side-by-side:%s:start -->" % group, "<!-- side-by-side:%s:end -->" % group


def fill(text, made):
    """-> (new text, list of groups without markers)."""
    missing = []
    for group, body in made.items():
        start, end = marks(group)
        a, b = text.find(start), text.find(end)
        if a < 0 or b < a:
            missing.append(group)
            continue
        text = text[:a + len(start)] + "\n" + body + "\n" + text[b:]
    return text, missing


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    check = "--check" in args
    billcall = load_billcall()
    prices = billcall.load_prices()
    bad = 0
    for path, lang in FILES:
        made = tables(billcall, prices, lang)
        try:
            with open(path, encoding="utf-8") as handle:
                text = handle.read()
        except OSError as exc:
            print("BAD: cannot read %s: %s" % (path, exc))
            bad += 1
            continue
        new, missing = fill(text, made)
        for group in missing:
            print("BAD: %s has no markers for the %s table" % (os.path.relpath(path, ROOT), group))
            bad += 1
        if new != text:
            if check:
                print("BAD: %s: the tables differ from data/prices.json - run tools/side_by_side.py"
                      % os.path.relpath(path, ROOT))
                bad += 1
            else:
                with open(path, "w", encoding="utf-8") as handle:
                    handle.write(new)
                print("written: %s" % os.path.relpath(path, ROOT))
    print("side_by_side: %d problem(s)" % bad)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
