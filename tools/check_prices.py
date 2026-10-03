#!/usr/bin/env python3
"""Check billcall's price table, data/prices.json.

Standard library only; it never touches the network. Every rule is a closed list, a date or a
number range, so a wrong row reddens this check before it can reach an estimate.

  python3 tools/check_prices.py data/prices.json              schema, closed lists, dates
  python3 tools/check_prices.py data/prices.json --strict     and every row read within max_age_days
  python3 tools/check_prices.py data/prices.json --today 2026-10-02

Exit code 0: no problems. 1: problems, one "BAD:" line each. 2: the file cannot be read or the
arguments are wrong. The last line always counts the rows and the problems.
"""
import datetime
import json
import math
import re
import sys

KINDS = ("seat", "api", "coding-plan", "token-plan", "hardware", "rent", "free-tier", "training", "platform")
USES = ("personal", "team-interactive", "automated")
STATUSES = ("official", "official-api", "secondary", "unverified", "stale")
PERIODS = ("month", "year", "one-off", "hour", "usage")
CURRENCIES = ("USD", "EUR", "CNY")
UNITS = ("seat-month", "seat-year", "account-month", "month", "year", "one-off", "gpu-hour", "hour", "credit",
         "1M-input", "1M-output", "1M-cached-input", "1M-cache-write")
REQUIRED = ("id", "vendor", "product", "kind", "use", "currency", "period", "prices", "jurisdiction",
            "checked", "url", "status")
OPTIONAL = ("min_seats", "max_seats", "modifiers", "quality_index", "quality_note", "tokenizer_factor",
            "endpoint_claude_code", "data_terms", "license", "valid_until", "limits", "price_note", "note", "models")
TEXT_FIELDS = ("quality_note", "data_terms", "license", "limits", "price_note", "note")
# A figure that was not read on the vendor's own page must say where it came from.
NOTE_NEEDED = ("secondary", "unverified", "stale")
# Every coding and token plan whose terms were read on 02.10.2026 forbids scripts, backends and
# other non-interactive use (Alibaba, MiMo, Z.ai, MiniMax, Kimi): people use them, scripts use tokens.
PEOPLE_ONLY_KINDS = ("coding-plan", "token-plan")
TOP_LEVEL = ("schema", "updated", "max_age_days", "about", "constants", "rows")
CONSTANT_FIELDS = ("id", "value", "high", "unit", "url", "checked", "status", "note")
DEFAULT_MAX_AGE_DAYS = 92

ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]*$")
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
JURISDICTION_RE = re.compile(r"^(?:[A-Z]{2}|local)$")

USAGE = "usage: check_prices.py PRICES.json [--strict] [--today YYYY-MM-DD]"


def parse_date(value):
    """'YYYY-MM-DD' -> datetime.date; anything else -> None."""
    if not isinstance(value, str) or not DATE_RE.match(value):
        return None
    try:
        return datetime.date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return None


def is_text(value):
    return isinstance(value, str) and value.strip() != ""


def is_number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def is_whole(value):
    return isinstance(value, int) and not isinstance(value, bool)


def is_https(value):
    return (isinstance(value, str) and value.startswith("https://") and len(value) > len("https://")
            and not any(ch.isspace() for ch in value))


def check_day(value, where, field, today, strict, max_age, stale):
    """A 'checked' date: a real date, not in the future, and in strict mode not older than max_age."""
    day = parse_date(value)
    if day is None:
        return ["%s: %s must be a date YYYY-MM-DD, got %r" % (where, field, value)]
    if day > today:
        return ["%s: %s %s is in the future" % (where, field, value)]
    if strict and not stale and (today - day).days > max_age:
        return ["%s: %s %s is older than %d days - read the page again, or set status stale"
                % (where, field, value, max_age)]
    return []


def check_price(price, where):
    if not isinstance(price, dict):
        return ["%s: not an object" % where]
    out = []
    for key in price:
        if key not in ("label", "amount", "unit"):
            out.append("%s: unknown field %s" % (where, key))
    if not is_text(price.get("label")):
        out.append("%s: label must be a non-empty string" % where)
    amount = price.get("amount")
    if not (is_number(amount) and amount >= 0):
        out.append("%s: amount must be a number of 0 or more, got %r" % (where, amount))
    if price.get("unit") not in UNITS:
        out.append("%s: unit %r is not one of: %s" % (where, price.get("unit"), ", ".join(UNITS)))
    return out


def check_modifier(modifier, where):
    if not isinstance(modifier, dict):
        return ["%s: not an object" % where]
    out = []
    for key in modifier:
        if key not in ("label", "factor"):
            out.append("%s: unknown field %s" % (where, key))
    if not is_text(modifier.get("label")):
        out.append("%s: label must be a non-empty string" % where)
    factor = modifier.get("factor")
    if not (is_number(factor) and 0 < factor <= 3):
        out.append("%s: factor must be a number above 0 and at most 3, got %r" % (where, factor))
    return out


def check_quality(quality, where):
    if not isinstance(quality, dict):
        return ["%s: quality_index must be an object or null" % where]
    out = []
    for key in quality:
        if key not in ("name", "value", "date", "url"):
            out.append("%s: quality_index has an unknown field %s" % (where, key))
    if not is_text(quality.get("name")):
        out.append("%s: quality_index.name must be a non-empty string" % where)
    if not is_number(quality.get("value")):
        out.append("%s: quality_index.value must be a number" % where)
    if parse_date(quality.get("date")) is None:
        out.append("%s: quality_index.date must be a date YYYY-MM-DD" % where)
    if not is_https(quality.get("url")):
        out.append("%s: quality_index.url must be an https:// address" % where)
    return out


def check_endpoint(endpoint, where):
    if not isinstance(endpoint, dict):
        return ["%s: endpoint_claude_code must be an object or null" % where]
    out = []
    for key in endpoint:
        if key not in ("supported", "doc", "note"):
            out.append("%s: endpoint_claude_code has an unknown field %s" % (where, key))
    if "supported" not in endpoint or not (endpoint["supported"] is None or isinstance(endpoint["supported"], bool)):
        out.append("%s: endpoint_claude_code.supported must be true, false or null" % where)
    if endpoint.get("doc") is not None and not is_https(endpoint.get("doc")):
        out.append("%s: endpoint_claude_code.doc must be an https:// address or null" % where)
    if "note" in endpoint and not is_text(endpoint["note"]):
        out.append("%s: endpoint_claude_code.note must be a non-empty string" % where)
    return out


MODEL_GROUPS = ("lead", "on_call")
MODEL_FIELDS = ("model", "quant", "license", "url")


def check_models(models, where):
    """models = {"lead": [...], "on_call": [...]}: which open-weight models run on this box or rented card."""
    if not isinstance(models, dict):
        return ["%s: models must be an object with lead and on_call" % where]
    out = []
    for key in models:
        if key not in MODEL_GROUPS:
            out.append("%s: models has an unknown field %s" % (where, key))
    if not isinstance(models.get("lead"), list) or not models.get("lead"):
        out.append("%s: models.lead must be a non-empty list" % where)
    if "on_call" in models and not isinstance(models["on_call"], list):
        out.append("%s: models.on_call must be a list" % where)
    for group in MODEL_GROUPS:
        items = models.get(group)
        if not isinstance(items, list):
            continue
        for i, item in enumerate(items, 1):
            at = "%s models.%s %d" % (where, group, i)
            if not isinstance(item, dict):
                out.append("%s: not an object" % at)
                continue
            for key in item:
                if key not in MODEL_FIELDS:
                    out.append("%s: unknown field %s" % (at, key))
            for key in ("model", "quant", "license"):
                if not is_text(item.get(key)):
                    out.append("%s: %s must be a non-empty string" % (at, key))
            if not is_https(item.get("url")):
                out.append("%s: url must be an https:// address" % at)
    return out


def check_row(row, n, today, strict, max_age):
    if not isinstance(row, dict):
        return ["row %d: not an object" % n]
    rid = row.get("id")
    where = "row %d (%s)" % (n, rid) if is_text(rid) else "row %d" % n
    out = []
    for key in REQUIRED:
        if key not in row:
            out.append("%s: missing %s" % (where, key))
    for key in row:
        if key not in REQUIRED and key not in OPTIONAL:
            out.append("%s: unknown field %s" % (where, key))
    if "id" in row and not (isinstance(rid, str) and ID_RE.match(rid)):
        out.append("%s: id must be lowercase letters, digits, dots and dashes" % where)
    for key in ("vendor", "product"):
        if key in row and not is_text(row[key]):
            out.append("%s: %s must be a non-empty string" % (where, key))
    for key, allowed in (("kind", KINDS), ("use", USES), ("status", STATUSES),
                         ("period", PERIODS), ("currency", CURRENCIES)):
        if key in row and row[key] not in allowed:
            out.append("%s: %s %r is not one of: %s" % (where, key, row[key], ", ".join(allowed)))
    if "url" in row and not is_https(row["url"]):
        out.append("%s: url must be an https:// address" % where)
    if "jurisdiction" in row and not (isinstance(row["jurisdiction"], str)
                                      and JURISDICTION_RE.match(row["jurisdiction"])):
        out.append("%s: jurisdiction must be a two-letter country code or 'local'" % where)
    if "checked" in row:
        out.extend(check_day(row["checked"], where, "checked", today, strict, max_age, row.get("status") == "stale"))
    if "valid_until" in row:
        until = parse_date(row["valid_until"])
        if until is None:
            out.append("%s: valid_until must be a date YYYY-MM-DD" % where)
        elif until < today:
            out.append("%s: valid_until %s has passed - the offer ended, update the row" % (where, row["valid_until"]))
    if "prices" in row:
        prices = row["prices"]
        if not isinstance(prices, list):
            out.append("%s: prices must be a list" % where)
        else:
            if not prices and not is_text(row.get("price_note")):
                out.append("%s: an empty prices list needs price_note saying why no price is published" % where)
            for i, price in enumerate(prices, 1):
                out.extend(check_price(price, "%s price %d" % (where, i)))
    if "modifiers" in row:
        modifiers = row["modifiers"]
        if not isinstance(modifiers, list) or not modifiers:
            out.append("%s: modifiers must be a non-empty list" % where)
        else:
            for i, modifier in enumerate(modifiers, 1):
                out.extend(check_modifier(modifier, "%s modifier %d" % (where, i)))
    for key in ("min_seats", "max_seats"):
        if key in row and row[key] is not None and not (is_whole(row[key]) and row[key] >= 1):
            out.append("%s: %s must be a whole number of at least 1, or null" % (where, key))
    low = row.get("min_seats")
    high = row.get("max_seats")
    if is_whole(low) and is_whole(high) and low > high:
        out.append("%s: min_seats %d is above max_seats %d" % (where, low, high))
    for key in TEXT_FIELDS:
        if key in row and not is_text(row[key]):
            out.append("%s: %s must be a non-empty string" % (where, key))
    if row.get("status") in NOTE_NEEDED and not is_text(row.get("note")):
        out.append("%s: status %s needs a note saying where the figure came from" % (where, row.get("status")))
    if "tokenizer_factor" in row and not (is_number(row["tokenizer_factor"]) and 0 < row["tokenizer_factor"] <= 3):
        out.append("%s: tokenizer_factor must be a number above 0 and at most 3" % where)
    if "quality_index" in row and row["quality_index"] is not None:
        out.extend(check_quality(row["quality_index"], where))
    if row.get("kind") == "api":
        if "quality_index" not in row:
            out.append("%s: an api row needs quality_index (an object, or null with quality_note)" % where)
        elif row["quality_index"] is None and not is_text(row.get("quality_note")):
            out.append("%s: quality_index is null - say why in quality_note" % where)
    if row.get("kind") == "free-tier" and not is_text(row.get("limits")):
        out.append("%s: a free-tier row needs limits" % where)
    if row.get("kind") in PEOPLE_ONLY_KINDS and row.get("use") == "automated":
        out.append("%s: a %s is for people, never for scripts - the terms read on 02.10.2026 forbid "
                   "non-interactive use; use personal or team-interactive" % (where, row.get("kind")))
    if "endpoint_claude_code" in row and row["endpoint_claude_code"] is not None:
        out.extend(check_endpoint(row["endpoint_claude_code"], where))
    if "models" in row:
        if row.get("kind") not in ("hardware", "rent"):
            out.append("%s: models belongs on hardware and rent rows only" % where)
        out.extend(check_models(row["models"], where))
    return out


def check_constant(item, n, today, strict, max_age):
    if not isinstance(item, dict):
        return ["constant %d: not an object" % n]
    cid = item.get("id")
    where = "constant %d (%s)" % (n, cid) if is_text(cid) else "constant %d" % n
    out = []
    for key in ("id", "value", "unit", "url", "checked", "status"):
        if key not in item:
            out.append("%s: missing %s" % (where, key))
    for key in item:
        if key not in CONSTANT_FIELDS:
            out.append("%s: unknown field %s" % (where, key))
    if "id" in item and not (isinstance(cid, str) and ID_RE.match(cid)):
        out.append("%s: id must be lowercase letters, digits, dots and dashes" % where)
    if "value" in item and not is_number(item["value"]):
        out.append("%s: value must be a number" % where)
    if "high" in item and not (is_number(item["high"]) and is_number(item.get("value"))
                               and item["high"] >= item["value"]):
        out.append("%s: high must be a number not below value (the top of a range)" % where)
    if "unit" in item and not is_text(item["unit"]):
        out.append("%s: unit must be a non-empty string" % where)
    if "url" in item and not is_https(item["url"]):
        out.append("%s: url must be an https:// address" % where)
    if "status" in item and item["status"] not in STATUSES:
        out.append("%s: status %r is not one of: %s" % (where, item["status"], ", ".join(STATUSES)))
    if "checked" in item:
        out.extend(check_day(item["checked"], where, "checked", today, strict, max_age, item.get("status") == "stale"))
    if "note" in item and not is_text(item["note"]):
        out.append("%s: note must be a non-empty string" % where)
    if item.get("status") in NOTE_NEEDED and not is_text(item.get("note")):
        out.append("%s: status %s needs a note saying where the figure came from" % (where, item.get("status")))
    return out


def check_document(doc, today, strict=False):
    """All problems of one parsed prices.json, as plain sentences. Never raises on bad data."""
    if not isinstance(doc, dict):
        return ["the file must hold one JSON object"]
    out = []
    for key in doc:
        if key not in TOP_LEVEL:
            out.append("unknown top-level field %s" % key)
    if doc.get("schema") != 1:
        out.append("schema must be 1")
    max_age = doc.get("max_age_days", DEFAULT_MAX_AGE_DAYS)
    if not (is_whole(max_age) and 1 <= max_age <= 366):
        out.append("max_age_days must be a whole number from 1 to 366")
        max_age = DEFAULT_MAX_AGE_DAYS
    updated = parse_date(doc.get("updated"))
    if updated is None:
        out.append("updated must be a date YYYY-MM-DD")
    elif updated > today:
        out.append("updated %s is in the future" % doc.get("updated"))
    rows = doc.get("rows")
    if not isinstance(rows, list) or not rows:
        out.append("rows must be a non-empty list")
        if not isinstance(rows, list):
            rows = []
    seen = set()
    for n, row in enumerate(rows, 1):
        out.extend(check_row(row, n, today, strict, max_age))
        rid = row.get("id") if isinstance(row, dict) else None
        if isinstance(rid, str):
            if rid in seen:
                out.append("row %d: duplicate id %s" % (n, rid))
            seen.add(rid)
    constants = doc.get("constants", [])
    if not isinstance(constants, list):
        out.append("constants must be a list")
        constants = []
    seen_constants = set()
    for n, item in enumerate(constants, 1):
        out.extend(check_constant(item, n, today, strict, max_age))
        cid = item.get("id") if isinstance(item, dict) else None
        if isinstance(cid, str):
            if cid in seen_constants:
                out.append("constant %d: duplicate id %s" % (n, cid))
            seen_constants.add(cid)
    return out


def load(path):
    """-> (document, None) or (None, the reason it cannot be read)."""
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle), None
    except (OSError, ValueError) as exc:
        return None, "cannot read %s: %s" % (path, exc)


def check_file(path, today=None, strict=False):
    """-> (problems, error, number of rows). error is set only when the file cannot be read."""
    doc, error = load(path)
    if error:
        return [], error, 0
    try:
        problems = check_document(doc, today or datetime.date.today(), strict)
    except Exception as exc:  # a bug in this checker must redden the run, never crash it
        problems = ["the checker itself failed: %r" % (exc,)]
    rows = doc.get("rows") if isinstance(doc, dict) else None
    return problems, None, (len(rows) if isinstance(rows, list) else 0)


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    strict = False
    today = datetime.date.today()
    path = None
    i = 0
    while i < len(args):
        arg = args[i]
        if arg == "--strict":
            strict = True
        elif arg == "--today":
            day = parse_date(args[i + 1]) if i + 1 < len(args) else None
            if day is None:
                print("check_prices: --today needs a date YYYY-MM-DD")
                return 2
            today = day
            i += 1
        elif arg.startswith("-") or path is not None:
            print(USAGE)
            return 2
        else:
            path = arg
        i += 1
    if path is None:
        print(USAGE)
        return 2
    problems, error, rows = check_file(path, today=today, strict=strict)
    if error:
        print("check_prices: " + error)
        return 2
    for problem in problems:
        print("BAD: " + problem)
    print("check_prices: %d row(s), %d problem(s)%s" % (rows, len(problems), ", strict" if strict else ""))
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
