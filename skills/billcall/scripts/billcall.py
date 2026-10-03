#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""billcall: what a company's AI costs, counted from one price table. It buys nothing.

Run it through the plugin's launcher (skills/billcall/SKILL.md says how):

  billcall.py estimate PROFILE.json [--policy FILE] [--json] [--today YYYY-MM-DD]
      The monthly bill of a mix: seats for people, API work for scripts, seats already paid for,
      the price of one accepted task, and every rule of the price table (and of the company
      policy) the mix breaks.
  billcall.py use --product ID --for scripts|people [--people N]
      May this row carry scripts (or this many people)? The check routecall's crew asks before it
      starts another program on a plan: exit 1 and the rule when the vendor's terms say no.
  billcall.py week [--days 7] [--logs DIR] [--team-csv FILE] [--roster FILE] [--otel FILE]
                   [--report DIR] [--lang xx] [--json]
      What was spent: Claude Code's own session logs on this machine (at list price, the way
      /usage counts), a Team or Enterprise spend-report CSV, an OpenTelemetry export; idle seats.
  billcall.py guard [--budget-day USD] [--budget-month USD] [--policy FILE] [--logs DIR] [--hook]
      Today's and this month's spend against a budget, at 50, 80 and 100 per cent.
  billcall.py contract --people N --usage USD
  billcall.py contract (--discount PCT | --rates FILE) [--target managed] [--out FILE]
      Team Premium against Enterprise for N people; the modelPricing block that makes Claude Code
      report spend at the company's contract rates (managed settings only).
  billcall.py local --hardware ID [--rent ID] [--watts W] [--hours-per-day H] [--months 36]
      An own machine against renting the same hours: the month's cost and the break-even load.
  billcall.py side-by-side us|china|local
      The US five (and Microsoft, GitHub), the Chinese vendors, or own machines and rent with the models
      to run on them, side by side as one Markdown table; every figure with its day and its page.
  billcall.py prices [--kind K] [--use U] [--vendor TEXT]
      Rows of the price table.

Standard library only, no network: every price comes from data/prices.json, and each row there
names the page and the day it was read. Exit 0: fine. 1: a rule is broken or a budget is spent -
the lines say which. 2: the input cannot be read. Never a traceback.
"""
import argparse
import csv
import datetime
import json
import os
import re
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
PRICES = os.path.join(ROOT, "data", "prices.json")
LANG_DIR = os.path.join(ROOT, "lang")

HOURS_PER_MONTH = 730.0            # 8760 hours a year / 12
TOKEN_FIELDS = (("input_mtok", "1M-input"), ("output_mtok", "1M-output"),
                ("cached_input_mtok", "1M-cached-input"), ("cache_write_mtok", "1M-cache-write"))
PEOPLE_KINDS = ("seat", "coding-plan", "token-plan", "training")
WORK_KINDS = ("api", "free-tier", "rent", "platform")
SEAT_UNITS = ("seat-month", "seat-year", "month", "year", "account-month")
ANNUAL_WORDS = ("annual", "annually", "yearly")
MONTHLY_WORDS = ("monthly",)
API_DAY = "claude-code-api-cost-per-active-day"
ELECTRICITY = "us-commercial-electricity"
API_ROW_PREFIX = "anthropic-api-"
COWORK = "cowork-usage-factor"
AGENT_SURFACES = ("cowork", "code")
# platform.claude.com/docs/en/about-claude/pricing (read 2026-10-02): a 1-hour cache write costs
# 2x the base input price; the table's own cache-write rows are the 5-minute ones (1.25x).
ONE_HOUR_WRITE = 2.0
# Same page: US-only inference (inference_geo "us") is 1.1x on every token category.
US_ONLY = 1.1
LEVELS = (100, 80, 50)
POLICY_NAME = "company-ai-policy.json"
MANAGED_DIRS = {
    "darwin": "/Library/Application Support/ClaudeCode",
    "linux": "/etc/claude-code",
    "win32": "C:\\Program Files\\ClaudeCode",
}


class Problem(Exception):
    """Input billcall cannot use. Printed as one line; exit 2."""


# ---------------------------------------------------------------- reading -------------------

def load_json(path, what):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError) as exc:
        raise Problem("cannot read %s %s: %s" % (what, path, exc))


def load_prices(path=None):
    """data/prices.json as {"rows": {id: row}, "constants": {id: item}, "updated": ..., ...}."""
    doc = load_json(path or PRICES, "the price table")
    if not isinstance(doc, dict) or not isinstance(doc.get("rows"), list):
        raise Problem("the price table %s has no rows" % (path or PRICES))
    rows = {}
    for row in doc["rows"]:
        if isinstance(row, dict) and isinstance(row.get("id"), str):
            rows[row["id"]] = row
    constants = {}
    for item in doc.get("constants") or []:
        if isinstance(item, dict) and isinstance(item.get("id"), str):
            constants[item["id"]] = item
    max_age = doc.get("max_age_days")
    return {"rows": rows, "constants": constants, "updated": doc.get("updated"),
            "max_age_days": max_age if isinstance(max_age, int) else 92}


def parse_day(value):
    if not isinstance(value, str) or not re.match(r"^\d{4}-\d{2}-\d{2}$", value):
        return None
    try:
        return datetime.date(int(value[0:4]), int(value[5:7]), int(value[8:10]))
    except ValueError:
        return None


TS_RE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})[T ](\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:?\d{2})?$")


def parse_ts(value):
    """An ISO time from a Claude Code log -> an aware UTC datetime, or None."""
    if not isinstance(value, str):
        return None
    match = TS_RE.match(value.strip())
    if not match:
        return None
    year, month, day, hour, minute, second, frac, zone = match.groups()
    try:
        micro = int((frac or "0")[:6].ljust(6, "0"))
        stamp = datetime.datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), micro)
    except ValueError:
        return None
    offset = datetime.timedelta(0)
    if zone and zone != "Z":
        sign = -1 if zone[0] == "-" else 1
        digits = zone[1:].replace(":", "")
        offset = sign * datetime.timedelta(hours=int(digits[:2]), minutes=int(digits[2:4]))
    return (stamp - offset).replace(tzinfo=datetime.timezone.utc)


def local_day(stamp):
    """The calendar day of an aware time on this computer's clock."""
    return datetime.datetime.fromtimestamp(stamp.timestamp()).date()


def money(amount, currency="USD"):
    sign = "-" if amount < 0 else ""
    text = "{:,.2f}".format(abs(amount))
    if text.endswith(".00"):
        text = text[:-3]
    if currency == "USD":
        return "%s$%s" % (sign, text)
    return "%s%s %s" % (sign, text, currency)


def number(value):
    """A count of tokens, people or hours from a profile: a non-negative number, or Problem."""
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value != value or value < 0:
        raise Problem("expected a number of 0 or more, got %r" % (value,))
    return float(value)


def lang_words(code):
    """lang/<code>.json, English underneath anything missing."""
    words = {}
    for name in ("en", code):
        path = os.path.join(LANG_DIR, "%s.json" % name)
        try:
            with open(path, encoding="utf-8") as handle:
                data = json.load(handle)
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            words.update(data)
    return words


def pick_lang(explicit=None, policy=None):
    for value in (explicit, (policy or {}).get("language"), os.environ.get("BILLCALL_LANG"),
                  os.environ.get("LC_ALL"), os.environ.get("LC_MESSAGES"), os.environ.get("LANG")):
        if isinstance(value, str) and len(value) >= 2:
            code = value[:2].lower()
            if os.path.exists(os.path.join(LANG_DIR, "%s.json" % code)):
                return code
    return "en"


# ---------------------------------------------------------------- the company policy file --

def policy_paths(cwd=None):
    """Where company-ai-policy.json is looked for, the binding one first: the managed copy an
    administrator installed, then $COMPANY_AI_POLICY, then the project, then the person's own."""
    out = []
    managed = MANAGED_DIRS.get(sys.platform)
    if managed:
        out.append(os.path.join(managed, POLICY_NAME))
    env = os.environ.get("COMPANY_AI_POLICY")
    if env:
        out.append(env)
    base = cwd or os.getcwd()
    out.append(os.path.join(base, POLICY_NAME))
    out.append(os.path.join(base, ".claude", POLICY_NAME))
    out.append(os.path.join(os.path.expanduser("~"), ".claude", POLICY_NAME))
    return out


def read_policy(explicit=None, cwd=None):
    """-> (policy dict, path) or ({}, None). A file that does not parse is a Problem, not silence."""
    for path in ([explicit] if explicit else policy_paths(cwd)):
        if path and os.path.isfile(path):
            data = load_json(path, "the company policy")
            if not isinstance(data, dict):
                raise Problem("%s must hold one JSON object" % path)
            return data, path
    if explicit:
        raise Problem("no company policy at %s" % explicit)
    return {}, None


def vendor_list(policy, key):
    value = (policy or {}).get(key) or []
    if not isinstance(value, list):
        raise Problem("%s in the company policy must be a list of vendor names" % key)
    return [str(v).strip().lower() for v in value if isinstance(v, str) and v.strip()]


def policy_rules(row, policy):
    """The company policy's own rules for one row: allowed_providers and deny_providers name
    vendors (matched case-insensitively inside the row's vendor). -> list of broken rules."""
    if not policy:
        return []
    out = []
    vendor = str(row.get("vendor", "")).lower()
    allowed = vendor_list(policy, "allowed_providers")
    denied = vendor_list(policy, "deny_providers")
    if allowed and not any(name in vendor for name in allowed):
        out.append("%s: the company policy allows only %s, and this row is %s" % (
            row["id"], ", ".join(allowed), row.get("vendor")))
    if any(name in vendor for name in denied):
        out.append("%s: the company policy denies %s" % (row["id"], row.get("vendor")))
    return out


# ---------------------------------------------------------------- prices of one row --------

def pick_price(row, billing=None, label=None, units=None):
    """-> (price, note). The price with the label asked for, else the one whose label says the
    billing asked for (annual / monthly), else the first; note says when the billing was absent."""
    prices = [p for p in row.get("prices") or []
              if isinstance(p, dict) and (units is None or p.get("unit") in units)]
    if not prices:
        return None, None
    if label:
        for price in prices:
            if price.get("label") == label:
                return price, None
        raise Problem("%s has no price labelled %r (it has: %s)" % (
            row["id"], label, "; ".join(str(p.get("label")) for p in prices)))
    words = ANNUAL_WORDS if billing == "annual" else MONTHLY_WORDS if billing == "monthly" else ()
    if words:
        for price in prices:
            text = str(price.get("label", "")).lower()
            if any(word in text for word in words):
                return price, None
        return prices[0], "no %s price on this row; used '%s'" % (billing, prices[0].get("label"))
    return prices[0], None


def monthly(price, people):
    """A seat-like price -> money per month for `people` people."""
    unit = price.get("unit")
    amount = float(price.get("amount") or 0)
    if unit == "seat-month":
        return amount * people
    if unit == "seat-year":
        return amount * people / 12.0
    if unit == "month":
        return amount * people          # a personal plan: one per person
    if unit == "year":
        return amount / 12.0
    if unit == "account-month":
        return amount
    raise Problem("price unit %s is not a monthly or yearly price" % unit)


def family(row):
    """Seats of one plan sold as tiers ('Claude Team - Standard seat', '... - Premium seat') share
    the plan's seat limits."""
    product = str(row.get("product", ""))
    return "%s|%s" % (row.get("vendor"), product.split(" - ")[0])


def age_note(row, today, max_age):
    day = parse_day(row.get("checked"))
    if day and today and (today - day).days > max_age:
        return "%s: price read on %s, more than %d days ago - re-read %s before relying on it" % (
            row["id"], row.get("checked"), max_age, row.get("url"))
    return None


def until_note(row, today):
    until = parse_day(row.get("valid_until"))
    if not until:
        return None
    if today and until < today:
        return "%s: the offer ended on %s - the row needs a new price" % (row["id"], row.get("valid_until"))
    return "%s: this price holds until %s%s" % (
        row["id"], row.get("valid_until"), (" (" + row["note"] + ")") if row.get("note") else "")


# ---------------------------------------------------------------- rules -----------------------

def people_rules(row, people, company_people):
    """The rules of the table for a row given to people. -> list of broken rules."""
    out = []
    if row.get("kind") not in PEOPLE_KINDS:
        out.append("%s is a %s row: people are counted on seats, coding plans, team token plans or "
                   "training; scripts and background jobs go under 'automated'" % (row["id"], row.get("kind")))
    if row.get("use") == "automated":
        out.append("%s is priced for scripts (use: automated), not as a seat for people" % row["id"])
    if row.get("use") == "personal" and company_people > 1:
        out.append("%s is a personal plan (use: personal): its terms cover one person, and a company of "
                   "%d people needs a team plan - one account per person, never a shared one"
                   % (row["id"], company_people))
    return out


def work_rules(row):
    """The rules of the table for a row given to scripts. -> list of broken rules."""
    out = []
    if row.get("use") != "automated":
        out.append("%s is for people (use: %s): scripts, CI and background jobs run only on rows with "
                   "use: automated - every coding and token plan read on 02.10.2026 forbids "
                   "non-interactive use" % (row["id"], row.get("use")))
    if row.get("kind") == "hardware":
        out.append("%s is a machine: count it under 'local' (or with the local command), not as "
                   "monthly work" % row["id"])
    elif row.get("kind") not in WORK_KINDS:
        out.append("%s is a %s row, not something scripts can be billed on" % (row["id"], row.get("kind")))
    return out


def seat_limits(families, rows):
    out = []
    for key, (people, ids) in sorted(families.items()):
        lows = [rows[i].get("min_seats") for i in ids if isinstance(rows[i].get("min_seats"), int)]
        highs = [rows[i].get("max_seats") for i in ids if isinstance(rows[i].get("max_seats"), int)]
        name = key.split("|", 1)[1]
        if lows and people < max(lows):
            out.append("%s is sold from %d seats; this mix has %d" % (name, max(lows), people))
        if highs and people > min(highs):
            out.append("%s is sold up to %d seats; this mix has %d" % (name, min(highs), people))
    return out


# ---------------------------------------------------------------- API work ------------------

def work_cost(row, work):
    """-> (money a month, description). API rows by tokens, rented machines by hours, free tiers
    at zero, platform rows by their own price or fee."""
    kind = row.get("kind")
    if kind == "free-tier":
        return 0.0, "free tier (%s)" % row.get("limits", "limits on the vendor's page")
    if kind == "rent":
        hours = number(work.get("hours_per_month", 0))
        gpus = number(work.get("gpus", 1))
        price, _ = pick_price(row, label=work.get("price_label"), units=("gpu-hour", "hour"))
        if price is None:
            raise Problem("%s has no hourly price" % row["id"])
        cost = float(price["amount"]) * hours * gpus
        return cost, "%s x %g h x %g GPU" % (money(float(price["amount"])), hours, gpus)
    if kind == "platform":
        price, _ = pick_price(row, label=work.get("price_label"), units=SEAT_UNITS)
        if price is not None:
            return monthly(price, 1), str(price.get("label"))
        spend = number(work.get("spend_usd", 0))
        factor = 1.0
        for modifier in row.get("modifiers") or []:
            factor *= float(modifier.get("factor") or 1)
        return spend * factor, "%s spent through it x %g" % (money(spend), factor)
    prices = [p for p in row.get("prices") or [] if isinstance(p, dict)]
    if not prices:
        raise Problem("%s has no published price: %s" % (row["id"], row.get("price_note", "")))
    tokenizer = 1.0
    if work.get("tokenizer", "previous") == "previous" and isinstance(row.get("tokenizer_factor"), (int, float)):
        tokenizer = float(row["tokenizer_factor"])
    factor = 1.0
    applied = []
    for wanted in work.get("modifiers") or []:
        hit = None
        for modifier in row.get("modifiers") or []:
            if str(modifier.get("label", "")).lower().startswith(str(wanted).lower()):
                hit = modifier
                break
        if hit is None:
            raise Problem("%s has no modifier starting with %r (it has: %s)" % (
                row["id"], wanted, "; ".join(str(m.get("label")) for m in row.get("modifiers") or []) or "none"))
        factor *= float(hit["factor"])
        applied.append("%s x%g" % (hit["label"], float(hit["factor"])))
    by_unit = {}
    for price in prices:
        by_unit.setdefault(price.get("unit"), float(price.get("amount") or 0))
    total = 0.0
    parts = []
    for field, unit in TOKEN_FIELDS:
        quantity = number(work.get(field, 0))
        if not quantity:
            continue
        rate = by_unit.get(unit)
        if rate is None and unit in ("1M-cached-input", "1M-cache-write"):
            rate = by_unit.get("1M-input")
            parts.append("%s at the input rate (the row lists no %s price)" % (field, unit))
        if rate is None:
            raise Problem("%s has no %s price" % (row["id"], unit))
        total += quantity * tokenizer * rate * factor
        parts.append("%gM %s x %s" % (quantity, field.replace("_mtok", "").replace("_", " "), money(rate)))
    if tokenizer != 1.0:
        parts.append("tokens x%g (newer tokenizer)" % tokenizer)
    parts.extend(applied)
    return total, ", ".join(parts) if parts else "no tokens given"


# ---------------------------------------------------------------- quality and surfaces -----

def quality_of(row):
    """The row's quality index -> (value, "name, date, url") or (None, None)."""
    item = row.get("quality_index")
    if isinstance(item, dict) and isinstance(item.get("value"), (int, float)) and not isinstance(item.get("value"), bool):
        return float(item["value"]), "%s, %s, %s" % (item.get("name", "quality index"), item.get("date"), item.get("url"))
    return None, None


def surface_note(name, role, row, people, table):
    """A role that works in an agent all day (Cowork or Claude Code) on the lowest seat tier: the shared
    usage pool runs out sooner. -> a note, or None. The factor comes from the price table's constants."""
    surface = str(role.get("surface", "")).lower()
    if surface not in AGENT_SURFACES or not people:
        return None
    item = table["constants"].get(COWORK)
    factor = ""
    if item and isinstance(item.get("value"), (int, float)):
        high = item.get("high")
        factor = " (%s%sx the usage of a chat, %s)" % (
            "%g" % float(item["value"]), "-%g" % float(high) if isinstance(high, (int, float)) else "", item.get("url"))
    where = "Cowork" if surface == "cowork" else "Claude Code"
    if "standard" in str(row.get("product", "")).lower():
        return ("%s: %d people work in %s, which draws on the same usage pool as chat at a higher rate%s - on a "
                "Standard seat they meet the limit first; a Premium seat gives 5x the usage of a Standard one: "
                "compare the two with an alternative" % (name, people, where, factor))
    return "%s: %d people work in %s, which draws on the same usage pool at a higher rate%s" % (name, people, where, factor)


# ---------------------------------------------------------------- estimate ------------------

def add(bucket, currency, amount):
    bucket[currency] = bucket.get(currency, 0.0) + amount


def estimate(profile, table, today=None, policy=None):
    """One profile -> {"lines", "broken", "notes", "new", "paid", "per_task", "alternatives"}."""
    if not isinstance(profile, dict):
        raise Problem("the profile must hold one JSON object")
    rows = table["rows"]
    max_age = table["max_age_days"]
    billing = profile.get("billing")
    roles = profile.get("roles") or []
    works = profile.get("automated") or []
    if not isinstance(roles, list) or not isinstance(works, list):
        raise Problem("roles and automated must be lists")
    if profile.get("people_total") is not None:
        company = int(number(profile["people_total"]))
    else:
        company = sum(int(number(r.get("people", 0))) for r in roles if isinstance(r, dict))
    active_days = number(profile.get("active_days_per_month", 21))
    out = {"name": profile.get("name", "profile"), "lines": [], "broken": [], "notes": [],
           "new": {}, "paid": {}, "per_task": None, "alternatives": [], "quality": []}
    families = {}
    fees_counted = set()
    for role in roles:
        if not isinstance(role, dict):
            raise Problem("every role must be an object")
        name = role.get("id", "role")
        people = int(number(role.get("people", 0)))
        product = role.get("product")
        bucket = out["paid"] if role.get("already_paid") else out["new"]
        if product == API_DAY:
            item = table["constants"].get(API_DAY)
            if not item:
                raise Problem("the price table has no constant %s" % API_DAY)
            each = float(item["value"]) * active_days
            add(bucket, "USD", each * people)
            out["lines"].append("%s: %d people on the API in Claude Code, about %s per active day x %g days = %s a "
                                "person, %s a month (%s)" % (name, people, money(float(item["value"])), active_days,
                                                             money(each), money(each * people), item.get("url")))
            continue
        row = rows.get(product)
        if row is None:
            out["broken"].append("%s: no row %r in the price table" % (name, product))
            continue
        out["broken"].extend("%s: %s" % (name, rule) for rule in people_rules(row, people, company))
        out["broken"].extend("%s: %s" % (name, rule) for rule in policy_rules(row, policy))
        price, note = pick_price(row, role.get("billing", billing), role.get("price_label"), SEAT_UNITS)
        if price is None:
            out["broken"].append("%s: %s has no published price (%s)" % (
                name, row["id"], row.get("price_note", "see its url")))
            continue
        amount = monthly(price, people)
        fees = [p for p in row.get("prices") or [] if isinstance(p, dict) and p.get("unit") == "account-month"
                and p is not price]
        for fee in fees:
            if (row["id"], fee.get("label")) not in fees_counted:
                fees_counted.add((row["id"], fee.get("label")))
                amount += float(fee["amount"])
                out["notes"].append("%s: plus %s %s a month once per account" % (
                    row["id"], money(float(fee["amount"])), fee.get("label")))
        currency = row.get("currency", "USD")
        add(bucket, currency, amount)
        out["lines"].append("%s: %d x %s, %s %s = %s a month%s" % (
            name, people, row.get("product"), price.get("label"), money(float(price["amount"]), currency),
            money(amount, currency), " (already paid)" if role.get("already_paid") else ""))
        if note:
            out["notes"].append("%s: %s" % (name, note))
        extra = surface_note(name, role, row, people, table)
        if extra:
            out["notes"].append(extra)
        key = family(row)
        held = families.setdefault(key, [0, set()])
        held[0] += people
        held[1].add(row["id"])
        for extra in (until_note(row, today), age_note(row, today, max_age)):
            if extra:
                out["notes"].append(extra)
        item = table["constants"].get(API_DAY)
        if item and row.get("vendor") == "Anthropic" and people and not role.get("already_paid"):
            out["notes"].append("%s: on the API instead, Claude Code averages about %s per active day x %g days = "
                                "%s a person (%s); the seat costs %s a person" % (
                                    name, money(float(item["value"])), active_days,
                                    money(float(item["value"]) * active_days), item.get("url"),
                                    money(amount / people, currency)))
    out["broken"].extend(seat_limits(dict((k, (v[0], v[1])) for k, v in families.items()), rows))
    for work in works:
        if not isinstance(work, dict):
            raise Problem("every automated item must be an object")
        name = work.get("id", "work")
        row = rows.get(work.get("product"))
        if row is None:
            out["broken"].append("%s: no row %r in the price table" % (name, work.get("product")))
            continue
        broken = work_rules(row) + policy_rules(row, policy)
        out["broken"].extend("%s: %s" % (name, rule) for rule in broken)
        if broken:
            continue
        cost, how = work_cost(row, work)
        currency = row.get("currency", "USD")
        add(out["new"], currency, cost)
        value, source = quality_of(row)
        out["lines"].append("%s (automated): %s: %s = %s a month%s" % (
            name, row.get("product"), how, money(cost, currency),
            "; quality index %g (%s)" % (value, source) if value is not None else ""))
        out["quality"].append({"id": name, "product": row["id"], "value": value, "cost": cost})
        for extra in (until_note(row, today), age_note(row, today, max_age)):
            if extra:
                out["notes"].append(extra)
    tasks = profile.get("tasks")
    if isinstance(tasks, dict) and out["new"].get("USD") is not None:
        per_month = number(tasks.get("per_month", 0))
        share = number(tasks.get("accepted_share", 1))
        accepted = per_month * min(share, 1.0)
        if accepted > 0:
            out["per_task"] = out["new"]["USD"] / accepted
            out["lines"].append("one accepted task: %s (%g tasks a month, %g%% accepted)" % (
                money(out["per_task"]), per_month, min(share, 1.0) * 100))
    for alternative in profile.get("alternatives") or []:
        if not isinstance(alternative, dict) or not isinstance(alternative.get("replace"), dict):
            raise Problem("an alternative needs a name and a replace object")
        changed = dict(profile)
        changed.pop("alternatives", None)
        changed["roles"] = []
        for role in roles:
            patch = alternative["replace"].get(role.get("id"))
            changed["roles"].append(dict(role, **patch) if isinstance(patch, dict) else role)
        changed["automated"] = []
        for work in works:
            patch = alternative["replace"].get(work.get("id")) if isinstance(work, dict) else None
            changed["automated"].append(dict(work, **patch) if isinstance(patch, dict) else work)
        other = estimate(changed, table, today, policy)
        out["alternatives"].append({"name": alternative.get("name", "alternative"),
                                    "new": other["new"], "paid": other["paid"], "broken": other["broken"],
                                    "quality": quality_change(out["quality"], other["quality"])})
    return out


def quality_change(before, after):
    """Automated work whose model changed in an alternative -> one sentence per change, naming the
    quality index on both sides, so a cheaper line that is also weaker is never shown as a plain saving."""
    out = []
    old = dict((item["id"], item) for item in before)
    for item in after:
        was = old.get(item["id"])
        if not was or was["product"] == item["product"]:
            continue
        if was["value"] is None or item["value"] is None:
            out.append("%s: %s -> %s, quality index not known for both - run a bench before switching" % (
                item["id"], was["product"], item["product"]))
            continue
        word = "lower" if item["value"] < was["value"] else "higher" if item["value"] > was["value"] else "the same"
        out.append("%s: %s -> %s, %s a month -> %s, quality index %g -> %g (%s)" % (
            item["id"], was["product"], item["product"], money(was["cost"]), money(item["cost"]),
            was["value"], item["value"], word))
    return out


def say_estimate(result, table):
    lines = ["billcall estimate - %s (price table of %s, data/prices.json)" % (result["name"], table.get("updated"))]
    lines.extend("  " + line for line in result["lines"])
    for currency, amount in sorted(result["new"].items()):
        lines.append("  new money a month: %s" % money(amount, currency))
    for currency, amount in sorted(result["paid"].items()):
        lines.append("  already paid for a month: %s" % money(amount, currency))
    if result["paid"] or result["new"]:
        totals = {}
        for bucket in (result["new"], result["paid"]):
            for currency, amount in bucket.items():
                add(totals, currency, amount)
        for currency, amount in sorted(totals.items()):
            lines.append("  the whole monthly bill: %s" % money(amount, currency))
    for alternative in result["alternatives"]:
        whole = {}
        for bucket in (alternative["new"], alternative["paid"]):
            for currency, amount in bucket.items():
                add(whole, currency, amount)
        lines.append("  alternative '%s': the whole monthly bill %s%s" % (
            alternative["name"], ", ".join(money(a, c) for c, a in sorted(whole.items())) or "-",
            " - breaks: " + "; ".join(alternative["broken"]) if alternative["broken"] else ""))
        for change in alternative.get("quality") or []:
            lines.append("    quality: " + change)
    for note in result["notes"]:
        lines.append("  note: " + note)
    if result["broken"]:
        for rule in result["broken"]:
            lines.append("  BROKEN: " + rule)
    else:
        lines.append("  rules of the price table: all kept")
    lines.append("  billcall counts and compares; it buys nothing - the person responsible buys.")
    return "\n".join(lines)


def use_check(table, product, purpose, people=1, policy=None):
    """May this row carry scripts, or this many people? -> list of broken rules (empty: yes)."""
    row = table["rows"].get(product)
    if row is None:
        raise Problem("no row %r in the price table (billcall.py prices lists them)" % product)
    if purpose == "scripts":
        return work_rules(row) + policy_rules(row, policy)
    broken = people_rules(row, people, people)
    low, high = row.get("min_seats"), row.get("max_seats")
    if isinstance(low, int) and people < low:
        broken.append("%s is sold from %d seats, and %d %s asked for" % (
            product, low, people, "person is" if people == 1 else "people are"))
    if isinstance(high, int) and people > high:
        broken.append("%s is sold up to %d seats, and %d people are asked for" % (product, high, people))
    return broken + policy_rules(row, policy)


# ---------------------------------------------------------------- Claude Code's logs --------

def log_roots(explicit=None):
    """Where Claude Code keeps session logs: $CLAUDE_CONFIG_DIR (comma-separated) or the two
    default folders."""
    if explicit:
        return [p for p in explicit if os.path.isdir(p)]
    roots = []
    for part in os.environ.get("CLAUDE_CONFIG_DIR", "").split(","):
        if part.strip():
            roots.append(os.path.join(os.path.expanduser(part.strip()), "projects"))
    if not roots:
        home = os.path.expanduser("~")
        roots = [os.path.join(home, ".config", "claude", "projects"), os.path.join(home, ".claude", "projects")]
    return [p for p in roots if os.path.isdir(p)]


def read_usage(roots, since=None, seconds=None):
    """Every priced API response in the logs since `since` (aware UTC), each counted once.

    Claude Code writes one line per content block, all carrying the same message id, request id
    and usage; counting lines would count a response several times. -> (entries, files, partial)."""
    seen = set()
    entries = []
    files = 0
    partial = False
    deadline = time.monotonic() + seconds if seconds else None
    floor = since.timestamp() - 86400 if since else None
    for root in roots:
        for folder, _dirs, names in os.walk(root):
            for name in names:
                if not name.endswith(".jsonl"):
                    continue
                path = os.path.join(folder, name)
                try:
                    if floor is not None and os.path.getmtime(path) < floor:
                        continue
                except OSError:
                    continue
                if deadline is not None and time.monotonic() > deadline:
                    partial = True
                    return entries, files, partial
                files += 1
                try:
                    handle = open(path, encoding="utf-8", errors="replace")
                except OSError:
                    continue
                with handle:
                    for line in handle:
                        if '"usage"' not in line:
                            continue
                        try:
                            record = json.loads(line)
                        except ValueError:
                            continue
                        message = record.get("message") if isinstance(record, dict) else None
                        usage = message.get("usage") if isinstance(message, dict) else None
                        if not isinstance(usage, dict):
                            continue
                        key = (message.get("id"), record.get("requestId"))
                        if key != (None, None):
                            if key in seen:
                                continue
                            seen.add(key)
                        stamp = parse_ts(record.get("timestamp"))
                        if stamp is None or (since is not None and stamp < since):
                            continue
                        entries.append(usage_entry(message.get("model"), usage, stamp))
    return entries, files, partial


def count(value):
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) and value > 0 else 0


def usage_entry(model, usage, stamp):
    split = usage.get("cache_creation") if isinstance(usage.get("cache_creation"), dict) else {}
    write_1h = count(split.get("ephemeral_1h_input_tokens"))
    write_5m = count(split.get("ephemeral_5m_input_tokens"))
    created = count(usage.get("cache_creation_input_tokens"))
    if not split:
        write_5m = created
    return {"model": str(model or "unknown"), "time": stamp, "input": count(usage.get("input_tokens")),
            "output": count(usage.get("output_tokens")), "read": count(usage.get("cache_read_input_tokens")),
            "write_5m": write_5m, "write_1h": write_1h, "us_only": usage.get("inference_geo") == "us"}


def model_row(model, rows):
    """The API row of a model id from a log ('claude-opus-5-5-20260901' -> its row), longest
    match first; None for a model the table does not price."""
    best = None
    for row_id in rows:
        if not row_id.startswith(API_ROW_PREFIX):
            continue
        stem = row_id[len(API_ROW_PREFIX):]
        rest = model[len(stem):] if model.startswith(stem) else None
        # 'claude-opus-5-6' is another version, not a dated 'claude-opus-5': a short number after the stem
        # names a version the table may not price, so it never takes an older row's price.
        if model == stem or (rest and rest[0] in "-[@" and not re.match(r"^-\d{1,2}(?:$|[-\[@])", rest)):
            if best is None or len(stem) > len(best[0]):
                best = (stem, rows[row_id])
    return best[1] if best else None


def entry_cost(entry, row):
    rates = {}
    for price in row.get("prices") or []:
        if isinstance(price, dict):
            rates.setdefault(price.get("unit"), float(price.get("amount") or 0))
    base = rates.get("1M-input")
    if base is None:
        return None
    cost = (entry["input"] * base + entry["output"] * rates.get("1M-output", 0)
            + entry["read"] * rates.get("1M-cached-input", base)
            + entry["write_5m"] * rates.get("1M-cache-write", base * 1.25)
            + entry["write_1h"] * base * ONE_HOUR_WRITE) / 1e6
    return cost * (US_ONLY if entry["us_only"] else 1.0)


def spend(entries, rows):
    """-> (total USD, {model: [tokens, USD or None]}, {day: USD})."""
    total = 0.0
    models = {}
    days = {}
    for entry in entries:
        row = model_row(entry["model"], rows)
        cost = entry_cost(entry, row) if row else None
        tokens = entry["input"] + entry["output"] + entry["read"] + entry["write_5m"] + entry["write_1h"]
        slot = models.setdefault(entry["model"], [0, 0.0 if cost is not None else None])
        slot[0] += tokens
        if cost is not None:
            slot[1] = (slot[1] or 0.0) + cost
            total += cost
            day = local_day(entry["time"]).isoformat()
            days[day] = days.get(day, 0.0) + cost
    return total, models, days


# ---------------------------------------------------------------- spend exports -------------

def amount_of(text):
    text = str(text or "").replace("$", "").replace(",", "").strip()
    try:
        return float(text) if text else 0.0
    except ValueError:
        return 0.0


def column(header, *words):
    for name in header:
        low = name.lower()
        if all(word in low for word in words):
            return name
    return None


def team_csv(path):
    """The spend report CSV of a Team or Enterprise organisation (one row per person and model)."""
    try:
        with open(path, encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            header = reader.fieldnames or []
            rows = list(reader)
    except (OSError, csv.Error, UnicodeDecodeError) as exc:
        raise Problem("cannot read the spend CSV %s: %s" % (path, exc))
    email = column(header, "email")
    if not email:
        raise Problem("%s has no e-mail column (its columns: %s)" % (path, ", ".join(header)))
    model = column(header, "model")
    product = column(header, "product")
    requests = column(header, "request")
    prompt = column(header, "prompt")
    completion = column(header, "completion")
    net = column(header, "net")
    gross = column(header, "gross")
    people = {}
    for row in rows:
        who = (row.get(email) or "").strip().lower()
        if not who:
            continue
        slot = people.setdefault(who, {"net": 0.0, "gross": 0.0, "requests": 0.0, "tokens": 0.0,
                                       "models": set(), "products": set()})
        slot["net"] += amount_of(row.get(net)) if net else 0.0
        slot["gross"] += amount_of(row.get(gross)) if gross else 0.0
        slot["requests"] += amount_of(row.get(requests)) if requests else 0.0
        slot["tokens"] += (amount_of(row.get(prompt)) if prompt else 0.0) + (amount_of(row.get(completion)) if completion else 0.0)
        if model and row.get(model):
            slot["models"].add(row[model].strip())
        if product and row.get(product):
            slot["products"].add(row[product].strip())
    return people


def roster(path):
    try:
        with open(path, encoding="utf-8-sig") as handle:
            names = [line.strip().lower() for line in handle]
    except OSError as exc:
        raise Problem("cannot read the roster %s: %s" % (path, exc))
    return sorted(set(n for n in names if n and not n.startswith("#") and "@" in n))


def otel_attrs(point):
    out = {}
    for item in point.get("attributes") or []:
        if not isinstance(item, dict):
            continue
        value = item.get("value") or {}
        for kind in ("stringValue", "intValue", "doubleValue", "boolValue"):
            if kind in value:
                out[item.get("key")] = value[kind]
                break
    return out


def otel_cost(path):
    """claude_code.cost.usage from an OTLP JSON export (one request per line, or one document):
    a cumulative series counts once at its largest value, a delta series is summed."""
    try:
        with open(path, encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise Problem("cannot read the OpenTelemetry export %s: %s" % (path, exc))
    docs = []
    try:
        docs.append(json.loads(text))
    except ValueError:
        for line in text.splitlines():
            line = line.strip()
            if line:
                try:
                    docs.append(json.loads(line))
                except ValueError:
                    continue
    series = {}
    for doc in docs:
        for resource in (doc.get("resourceMetrics") or []) if isinstance(doc, dict) else []:
            for scope in resource.get("scopeMetrics") or []:
                for metric in scope.get("metrics") or []:
                    if metric.get("name") != "claude_code.cost.usage":
                        continue
                    data = metric.get("sum") or metric.get("gauge") or {}
                    cumulative = data.get("aggregationTemporality") in (2, "AGGREGATION_TEMPORALITY_CUMULATIVE")
                    for point in data.get("dataPoints") or []:
                        attrs = otel_attrs(point)
                        value = point.get("asDouble", point.get("asInt", 0))
                        try:
                            value = float(value)
                        except (TypeError, ValueError):
                            continue
                        key = (tuple(sorted((str(k), str(v)) for k, v in attrs.items())),
                               point.get("startTimeUnixNano"), cumulative)
                        if cumulative:
                            series[key] = max(series.get(key, 0.0), value)
                        else:
                            series[key] = series.get(key, 0.0) + value
    people = {}
    for (attrs, _start, _cumulative), value in series.items():
        attrs = dict(attrs)
        who = str(attrs.get("user.email") or attrs.get("user.account_uuid") or "unknown")
        model = str(attrs.get("model") or "unknown")
        slot = people.setdefault(who, {})
        slot[model] = slot.get(model, 0.0) + value
    return people


def week(args, table):
    days = max(1, int(args.days))
    now = datetime.datetime.now(datetime.timezone.utc)
    since = now - datetime.timedelta(days=days)
    out = {"days": days, "logs": None, "team": None, "otel": None, "idle": None}
    roots = log_roots(args.logs)
    entries, files, partial = read_usage(roots, since)
    total, models, by_day = spend(entries, table["rows"])
    out["logs"] = {"roots": roots, "files": files, "responses": len(entries), "usd": total,
                   "models": models, "days": by_day, "partial": partial}
    if args.team_csv:
        out["team"] = team_csv(args.team_csv)
    if args.otel:
        out["otel"] = otel_cost(args.otel)
    if args.roster:
        if out["team"] is None:
            raise Problem("--roster is compared with a spend report: give --team-csv too")
        names = roster(args.roster)
        floor = float(args.idle_below)
        out["idle"] = [n for n in names if out["team"].get(n, {}).get("requests", 0.0) < floor]
    return out


def say_week(result, words):
    lines = ["# %s" % words.get("week_title", "AI this week")]
    logs = result["logs"]
    lines.append("")
    lines.append("## %s" % words.get("week_this_machine", "This computer (Claude Code logs, list price)"))
    if not logs["roots"]:
        lines.append("- no Claude Code log folder on this computer (looked in $CLAUDE_CONFIG_DIR, ~/.config/claude, ~/.claude)")
    else:
        lines.append("- %d responses in %d log files over %d days: %s" % (
            logs["responses"], logs["files"], result["days"], money(logs["usd"])))
        for day in sorted(logs["days"]):
            lines.append("  - %s: %s" % (day, money(logs["days"][day])))
        for model in sorted(logs["models"]):
            tokens, cost = logs["models"][model]
            lines.append("  - %s: %s tokens, %s" % (model, "{:,}".format(tokens),
                                                     money(cost) if cost is not None else "not in the price table"))
        lines.append("- a subscription seat is not billed by the token: this is what the same work costs on the API")
    if result["team"] is not None:
        lines.append("")
        lines.append("## %s" % words.get("week_team", "The organisation's spend report"))
        team = result["team"]
        lines.append("- %d people, net %s, gross %s" % (
            len(team), money(sum(p["net"] for p in team.values())), money(sum(p["gross"] for p in team.values()))))
        for who in sorted(team, key=lambda k: -team[k]["net"])[:20]:
            person = team[who]
            lines.append("  - %s: net %s, %d requests, %s" % (who, money(person["net"]), int(person["requests"]),
                                                             ", ".join(sorted(person["models"])) or "-"))
    if result["otel"] is not None:
        lines.append("")
        lines.append("## %s" % words.get("week_otel", "OpenTelemetry (claude_code.cost.usage)"))
        for who in sorted(result["otel"]):
            models = result["otel"][who]
            lines.append("- %s: %s (%s)" % (who, money(sum(models.values())),
                                            ", ".join("%s %s" % (m, money(v)) for m, v in sorted(models.items()))))
    if result["idle"] is not None:
        lines.append("")
        lines.append("## %s" % words.get("week_idle", "Seats nobody used"))
        if result["idle"]:
            lines.extend("- %s" % who for who in result["idle"])
        else:
            lines.append("- none")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------- guard ---------------------

def budget_of(args, policy):
    day = args.budget_day if args.budget_day is not None else policy.get("max_usd_per_day")
    month = args.budget_month if args.budget_month is not None else policy.get("max_usd_per_month")
    path = os.path.join(os.path.expanduser("~"), ".billcall", "budget.json")
    if day is None and month is None and os.path.isfile(path):
        own = load_json(path, "the budget")
        if isinstance(own, dict):
            day, month = own.get("day"), own.get("month")
    for value in (day, month):
        if value is not None:
            number(value)
    return (float(day) if day else None), (float(month) if month else None)


def level(spent, budget):
    if not budget:
        return None
    share = spent * 100.0 / budget
    for mark in LEVELS:
        if share >= mark:
            return mark
    return 0


def guard(args, table, now=None):
    cwd = None
    if args.hook:
        try:
            payload = json.loads(sys.stdin.read() or "{}")
            cwd = payload.get("cwd") if isinstance(payload, dict) else None
        except ValueError:
            cwd = None
    policy, _where = read_policy(args.policy, cwd)
    day_budget, month_budget = budget_of(args, policy)
    if day_budget is None and month_budget is None:
        return None
    now = now or datetime.datetime.now(datetime.timezone.utc)
    local_now = datetime.datetime.fromtimestamp(now.timestamp())
    month_start = local_now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    since = datetime.datetime.fromtimestamp(month_start.timestamp(), datetime.timezone.utc)
    entries, _files, partial = read_usage(log_roots(args.logs), since, seconds=4 if args.hook else None)
    _total, _models, by_day = spend(entries, table["rows"])
    today = local_now.date().isoformat()
    spent_day = by_day.get(today, 0.0)
    spent_month = sum(by_day.values())
    return {"day": (spent_day, day_budget, level(spent_day, day_budget)),
            "month": (spent_month, month_budget, level(spent_month, month_budget)),
            "partial": partial, "lang": pick_lang(getattr(args, "lang", None), policy)}


def say_guard(result, words=None):
    """The one budget line, in the person's language (lang/<code>.json, English underneath)."""
    words = words or lang_words("en")
    parts = []
    worst = 0
    for name in ("day", "month"):
        spent, budget, mark = result[name]
        if budget:
            worst = max(worst, mark or 0)
            parts.append(words["guard_" + name].format(spent=money(spent), budget=money(budget),
                                                       share=int(spent * 100 / budget)))
    head = words["guard_head"].format(parts=", ".join(parts))
    if worst >= 100:
        head += words["guard_spent"]
    elif worst >= 80:
        head += words["guard_80"]
    elif worst >= 50:
        head += words["guard_50"]
    if result["partial"]:
        head += words["guard_partial"]
    return worst, head


# ---------------------------------------------------------------- side by side ----------------

US_FIVE = ("Anthropic", "OpenAI", "Google", "xAI", "Meta")
US_EXTRA = ("Microsoft", "GitHub")
CHINA = ("DeepSeek", "Moonshot AI", "Alibaba Cloud", "Z.ai", "MiniMax", "Xiaomi", "BytePlus")
GROUPS = ("us", "china", "local")
UNIT_WORDS = {"seat-month": "a seat a month", "seat-year": "a seat a year", "month": "a month",
              "year": "a year", "account-month": "an account a month", "1M-input": "1M in",
              "1M-output": "1M out", "1M-cached-input": "1M cached in", "1M-cache-write": "1M cache write",
              "gpu-hour": "a GPU-hour", "hour": "an hour", "one-off": "once"}


def cell_text(value):
    """Text that is safe inside a Markdown table cell."""
    return " ".join(str(value).replace("|", "/").split())


def row_cell(row, short=False, name=True):
    """One row as 'Product: price, price; seats (read [YYYY-MM-DD](page))'."""
    prices = [p for p in row.get("prices") or [] if isinstance(p, dict) and isinstance(p.get("amount"), (int, float))]
    currency = row.get("currency", "USD")
    if prices:
        said = "; ".join("%s %s%s" % (money(float(p["amount"]), currency), UNIT_WORDS.get(p.get("unit"), p.get("unit", "")),
                                     "" if short or not p.get("label") else " (%s)" % p["label"]) for p in prices)
    else:
        said = "no public price: " + str(row.get("price_note") or "ask the vendor")
    seats = []
    if isinstance(row.get("min_seats"), int):
        seats.append("from %d seats" % row["min_seats"])
    if isinstance(row.get("max_seats"), int) and row["max_seats"] > 1:
        seats.append("up to %d" % row["max_seats"])
    until = (" until %s" % row["valid_until"]) if row.get("valid_until") else ""
    status = "" if row.get("status") in ("official", "official-api") else " [%s]" % row.get("status")
    return cell_text("%s%s%s%s (read [%s](%s))%s" % (
        "%s: " % row.get("product") if name else "", said, (", " + " ".join(seats)) if seats else "", until,
        row.get("checked"), row.get("url"), status))


def input_price(row):
    for price in row.get("prices") or []:
        if isinstance(price, dict) and price.get("unit") == "1M-input" and isinstance(price.get("amount"), (int, float)):
            return float(price["amount"])
    return None


def by_vendor(table, vendor):
    return [table["rows"][i] for i in sorted(table["rows"]) if table["rows"][i].get("vendor") == vendor]


def us_line(table, vendor):
    rows = by_vendor(table, vendor)
    people = [r for r in rows if r.get("kind") == "seat" and r.get("use") == "team-interactive"
              and "enterprise" not in str(r.get("product", "")).lower()]
    contract = [r for r in rows if "enterprise" in str(r.get("product", "")).lower() and r.get("kind") in ("seat", "platform")]
    personal = [r for r in rows if r.get("use") == "personal" and r.get("kind") in ("seat", "coding-plan", "token-plan")]
    api = [r for r in rows if r.get("kind") == "api" and input_price(r) is not None]
    best = sorted(api, key=lambda r: -(quality_of(r)[0] or -1))[:1]
    cheap = sorted(api, key=input_price)[:1]
    agents = [r for r in rows if r.get("kind") == "coding-plan"]
    join = lambda items: "<br>".join(row_cell(r) for r in items) or "-"
    flag = "<br>".join(row_cell(r, short=True) + ("; quality index %g" % quality_of(r)[0] if quality_of(r)[0] is not None else "")
                       for r in best) or "-"
    return [vendor, join(people), join(contract), join(personal), flag,
            "<br>".join(row_cell(r, short=True) for r in cheap if r not in best) or (flag if cheap else "-"), join(agents)]


def china_line(table, vendor):
    rows = by_vendor(table, vendor)
    api = sorted((r for r in rows if r.get("kind") == "api"), key=lambda r: (input_price(r) is None, input_price(r) or 0))
    people = [r for r in rows if r.get("kind") in ("seat", "coding-plan", "token-plan")]
    first = (api or people or [{}])[0]
    terms = cell_text("; ".join(x for x in (
        "data processed in %s" % first.get("jurisdiction") if first.get("jurisdiction") else "",
        str(first.get("data_terms") or ""), "weights: %s" % first["license"] if first.get("license") else "") if x)) or "-"
    return [vendor, "<br>".join(row_cell(r, short=True) for r in api) or "-",
            "<br>".join(row_cell(r) + (" - for one person" if r.get("use") == "personal" else "") for r in people) or "-",
            terms]


def model_list(items):
    out = []
    for item in items or []:
        if isinstance(item, dict):
            out.append(cell_text("[%s](%s) %s (%s)" % (item.get("model"), item.get("url"), item.get("quant", ""),
                                                      item.get("license", "?"))))
    return ", ".join(out) or "-"


def local_line(row):
    models = row.get("models") if isinstance(row.get("models"), dict) else {}
    return [cell_text(row.get("product")), row_cell(row, name=False), model_list(models.get("lead")),
            model_list(models.get("on_call"))]


def side_by_side(table, group):
    """One of the owner's three tables from the price table: every figure with its day and its page.
    -> (header, rows)."""
    if group == "us":
        header = ["Vendor", "Seats for a team", "Enterprise / direct contract", "Plans for one person",
                  "Strongest API ($ per 1M)", "Cheapest API ($ per 1M)", "Coding agent plans"]
        return header, [us_line(table, v) for v in US_FIVE + US_EXTRA if by_vendor(table, v)]
    if group == "china":
        header = ["Vendor", "API ($ per 1M tokens)", "Plans for people", "Data and weights"]
        return header, [china_line(table, v) for v in CHINA if by_vendor(table, v)]
    if group == "local":
        header = ["Machine or rent", "Price", "Lead model", "On call"]
        rows = [table["rows"][i] for i in sorted(table["rows"]) if table["rows"][i].get("kind") in ("hardware", "rent")]
        rows.sort(key=lambda r: (r.get("kind") != "hardware", input_price(r) or 0))
        return header, [local_line(r) for r in rows]
    raise Problem("no group %r (there are: %s)" % (group, ", ".join(GROUPS)))


def say_side_by_side(header, lines):
    out = ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)]
    out.extend("| " + " | ".join(line) + " |" for line in lines)
    return "\n".join(out)


# ---------------------------------------------------------------- cheap class ratio ---------

# The cheap class: each Chinese vendor's own cheapest general model with a list price, at the vendor's own
# API (not a reseller). Moonshot has no flash-class model, so Kimi K2.7-code stands in and is kept out of
# the headline range (CHEAP_CHINA_HEADLINE).
CHEAP_CHINA = ("deepseek-api-flash", "alibaba-api-qwen3-8-flash", "zai-api-glm-5-3-flash",
               "xiaomi-api-mimo-v2-6-flash", "minimax-api-m3", "moonshot-api-kimi-k2-7-code")
CHEAP_CHINA_HEADLINE = CHEAP_CHINA[:5]
# What they are set against: Claude Haiku and Sonnet, and the cheap models of OpenAI and Google.
CHEAP_REFERENCE = ("anthropic-api-claude-haiku-4-5", "anthropic-api-claude-sonnet-5-5", "google-api-gemini-3-8-flash",
                   "openai-api-gpt-6-luna", "google-api-gemini-2-5-flash-lite")
# Input tokens to one output token in the blended price (the Artificial Analysis convention, 3:1).
BLEND_INPUT, BLEND_OUTPUT = 3.0, 1.0


def first_rate(row, unit):
    """The row's first price in a unit (the lowest prompt tier, the peak hour) -> float or None."""
    for price in row.get("prices") or []:
        if isinstance(price, dict) and price.get("unit") == unit and isinstance(price.get("amount"), (int, float)):
            return float(price["amount"])
    return None


def blended(row):
    """$ per 1M tokens at 3 input : 1 output, list price, no cache, no batch -> float or None."""
    rate_in, rate_out = first_rate(row, "1M-input"), first_rate(row, "1M-output")
    if rate_in is None or rate_out is None:
        return None
    return (BLEND_INPUT * rate_in + BLEND_OUTPUT * rate_out) / (BLEND_INPUT + BLEND_OUTPUT)


def times(a, b):
    """How many times a is dearer than b -> float or None (b free or unknown)."""
    if a is None or b is None or b <= 0:
        return None
    return a / b


def say_times(value):
    if value is None:
        return "-"
    return ("x%.1f" % value) if value >= 1 else ("x%.2f" % value)


def quality_word(cheap, ref):
    """The cheap model against the reference on the quality index -> 'weaker 39 vs 56' and the like."""
    a, b = quality_of(cheap)[0], quality_of(ref)[0]
    if a is None or b is None:
        return "quality index not known for both - bench before switching"
    if abs(a - b) <= 2:
        word = "about the same"
    else:
        word = "weaker" if a < b else "stronger"
    return "%s, index %g vs %g" % (word, a, b)


def pair(cheap, ref):
    """One cheap Chinese row against one reference row -> dict of ratios and the quality word."""
    return {"cheap": cheap["id"], "ref": ref["id"],
            "input": times(first_rate(ref, "1M-input"), first_rate(cheap, "1M-input")),
            "output": times(first_rate(ref, "1M-output"), first_rate(cheap, "1M-output")),
            "blended": times(blended(ref), blended(cheap)),
            "quality": quality_word(cheap, ref)}


def short_price(row):
    return "%s in / %s out" % (money(first_rate(row, "1M-input") or 0), money(first_rate(row, "1M-output") or 0))


def cheap_class(table):
    """How many times the cheap Chinese models are cheaper than Claude Haiku / Sonnet, GPT-6 Luna and the Gemini
    Flash models, per 1M tokens at list price. A ratio above 1 means the Chinese model is cheaper.
    -> {"header", "lines", "pairs", "ranges": {ref id: (low, high, quality words)}}."""
    rows = table["rows"]
    cheap = [rows[i] for i in CHEAP_CHINA if i in rows]
    refs = [rows[i] for i in CHEAP_REFERENCE if i in rows]
    header = ["Chinese model ($ per 1M)", "Quality index"]
    header += ["vs %s (%s)" % (str(r.get("product")).split(" - ")[-1], short_price(r)) for r in refs]
    lines, pairs = [], []
    for row in cheap:
        value = quality_of(row)[0]
        line = [row_cell(row, short=True), ("%g" % value) if value is not None else "-"]
        for ref in refs:
            one = pair(row, ref)
            pairs.append(one)
            line.append(cell_text("%s (in %s, out %s); %s" % (say_times(one["blended"]), say_times(one["input"]),
                                                              say_times(one["output"]), one["quality"])))
        lines.append(line)
    ranges = {}
    for ref in refs:
        got = [p for p in pairs if p["ref"] == ref["id"] and p["cheap"] in CHEAP_CHINA_HEADLINE and p["blended"] is not None]
        if got:
            known = [quality_of(rows[p["cheap"]])[0] for p in got if quality_of(rows[p["cheap"]])[0] is not None]
            ranges[ref["id"]] = (min(p["blended"] for p in got), max(p["blended"] for p in got),
                                 (min(known), max(known)) if known else None, quality_of(ref)[0],
                                 len(got) - len(known))
    return {"header": [cell_text(h) for h in header], "lines": lines, "pairs": pairs, "ranges": ranges}


def say_cheap_class(result, table, words=None):
    """The table, then one headline line and its conditions, in the person's language (lang/*.json)."""
    words = words or lang_words("en")
    rows = table["rows"]
    out = [say_side_by_side(result["header"], result["lines"]), ""]
    ranges = result["ranges"]
    claude = [ranges[i] for i in CHEAP_REFERENCE[:2] if i in ranges]
    others = [ranges[i] for i in CHEAP_REFERENCE[2:] if i in ranges]
    if claude:
        out.append(words["ratio_headline"].format(
            low="%.1f" % min(r[0] for r in claude), high="%.1f" % max(r[1] for r in claude),
            haiku=say_range(ranges.get(CHEAP_REFERENCE[0])), sonnet=say_range(ranges.get(CHEAP_REFERENCE[1]))))
    for ref_id in CHEAP_REFERENCE[2:]:
        if ref_id in ranges:
            out.append(words["ratio_other"].format(model=str(rows[ref_id].get("product")).split(" - ")[-1],
                                                   range=say_range(ranges[ref_id])))
    if others:
        out.append("")
    out.append(words["ratio_conditions"].format(updated=table.get("updated")))
    return "\n".join(out)


def say_range(item):
    """(low, high, Chinese index range, reference index, rows without an index) -> 'x3.8-x11.4 (index 29-42 vs 17)'."""
    if not item:
        return "-"
    low, high, cheap_q, ref_q, unknown = item
    say = lambda v: ("x%.1f" % v) if v >= 1 else ("x%.2f" % v)
    if cheap_q is None or ref_q is None:
        quality = "quality index not known for both - bench before switching"
    else:
        quality = "index %g-%g vs %g" % (cheap_q[0], cheap_q[1], ref_q)
        if cheap_q[1] < ref_q - 2:
            quality += ", all weaker"
        elif cheap_q[0] > ref_q + 2:
            quality += ", all stronger"
        if unknown:
            quality += "; %d without an index" % unknown
    return "%s-%s (%s)" % (say(low), say(high), quality)


# ---------------------------------------------------------------- contract ------------------

MODEL_PRICING_RATES = ("input", "output", "cacheRead", "cacheWrite")
TARGET_REFUSAL = ("Claude Code reads modelPricing only from managed settings: 'Claude Code ignores the key in "
                  "user, project, and local settings and in --settings' (code.claude.com/docs/en/costs, read "
                  "2026-10-02). Deliver it as server-managed settings, an MDM policy or managed-settings.json.")


def seats_against_enterprise(table, people, usage, billing="annual"):
    rows = table["rows"]
    premium_row = rows.get("anthropic-team-premium")
    standard_row = rows.get("anthropic-team-standard")
    enterprise_row = rows.get("anthropic-enterprise")
    if not (premium_row and standard_row and enterprise_row):
        raise Problem("the price table lacks a Claude Team or Enterprise row")
    premium = float(pick_price(premium_row, billing, units=SEAT_UNITS)[0]["amount"])
    standard = float(pick_price(standard_row, billing, units=SEAT_UNITS)[0]["amount"])
    seat = float(pick_price(enterprise_row, units=SEAT_UNITS)[0]["amount"])
    floor = enterprise_row.get("min_seats") or 1
    team_max = premium_row.get("max_seats")
    seats = max(people, floor)
    enterprise = seats * seat + people * usage
    even = (people * premium - seats * seat) / float(people) if people else 0.0
    return {"people": people, "usage": usage, "premium": people * premium, "standard": people * standard,
            "enterprise": enterprise, "enterprise_seats": seats, "break_even_usage": even,
            "team_fits": team_max is None or people <= team_max, "team_max": team_max}


def say_seats(result):
    lines = ["billcall contract - %d people, API use of %s a person a month on Enterprise" % (
        result["people"], money(result["usage"]))]
    lines.append("  Claude Team, all Premium: %s a month%s" % (
        money(result["premium"]), "" if result["team_fits"] else " - Team stops at %d seats" % result["team_max"]))
    lines.append("  Claude Team, all Standard: %s a month" % money(result["standard"]))
    lines.append("  Claude Enterprise: %d seats x seat price + usage = %s a month" % (
        result["enterprise_seats"], money(result["enterprise"])))
    lines.append("  Enterprise costs less than all-Premium while API use stays under %s a person a month" % (
        money(result["break_even_usage"])))
    return "\n".join(lines)


def model_pricing(rates):
    """A rates object -> the modelPricing block of managed settings, checked against the shape on
    code.claude.com/docs/en/settings-reference (read 2026-10-02)."""
    if not isinstance(rates, dict):
        raise Problem("the rates file must hold one JSON object")
    block = {}
    if "multiplier" in rates:
        value = rates["multiplier"]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 < value <= 10:
            raise Problem("multiplier must be a number above 0 and at most 10, got %r" % (value,))
        block["multiplier"] = value
    if "overrides" in rates:
        overrides = rates["overrides"]
        if not isinstance(overrides, dict) or not overrides:
            raise Problem("overrides must map model ids to rates")
        clean = {}
        for model, row in overrides.items():
            if not isinstance(row, dict) or sorted(row) != sorted(MODEL_PRICING_RATES):
                raise Problem("%s needs exactly %s" % (model, ", ".join(MODEL_PRICING_RATES)))
            for name in MODEL_PRICING_RATES:
                value = row[name]
                if isinstance(value, bool) or not isinstance(value, (int, float)) or not 0 <= value <= 10000:
                    raise Problem("%s.%s must be a number from 0 to 10000 (USD per million tokens)" % (model, name))
            clean[model] = dict((name, row[name]) for name in MODEL_PRICING_RATES)
        block["overrides"] = clean
    unknown = sorted(set(rates) - {"multiplier", "overrides"})
    if unknown:
        raise Problem("unknown fields in the rates: %s" % ", ".join(unknown))
    if not block:
        raise Problem("the rates need a multiplier, overrides, or both")
    return {"modelPricing": block}


# ---------------------------------------------------------------- local ---------------------

def local_payback(table, hardware, rent=None, watts=None, hours_per_day=24.0, months=36):
    rows = table["rows"]
    machine = rows.get(hardware)
    if machine is None or machine.get("kind") != "hardware":
        raise Problem("%r is not a hardware row of the price table" % hardware)
    price, _ = pick_price(machine, units=("one-off",))
    if price is None:
        raise Problem("%s has no one-off price" % hardware)
    if months <= 0 or not 0 < hours_per_day <= 24:
        raise Problem("months must be above 0 and hours per day from above 0 to 24")
    hours = HOURS_PER_MONTH * hours_per_day / 24.0
    own_fixed = float(price["amount"]) / months
    rate = None
    item = table["constants"].get(ELECTRICITY)
    if watts is not None and item:
        rate = float(item["value"])
    power = (watts / 1000.0) * hours * rate if rate is not None else None
    out = {"hardware": hardware, "product": machine.get("product"), "price": float(price["amount"]),
           "months": months, "hours": hours, "own_fixed": own_fixed, "power": power,
           "own": own_fixed + (power or 0.0), "rent": None, "break_even_share": None, "rent_row": rent}
    if rent:
        hired = rows.get(rent)
        if hired is None or hired.get("kind") != "rent":
            raise Problem("%r is not a rent row of the price table" % rent)
        hourly, _ = pick_price(hired, units=("gpu-hour", "hour"))
        if hourly is None:
            raise Problem("%s has no hourly price" % rent)
        per_hour = float(hourly["amount"])
        out["rent"] = per_hour * hours
        margin = per_hour - ((watts / 1000.0) * rate if rate is not None else 0.0)
        if margin > 0:
            out["break_even_share"] = own_fixed / (HOURS_PER_MONTH * margin)
    return out


def say_local(result):
    lines = ["billcall local - %s, %s once, spread over %d months" % (
        result["product"], money(result["price"]), result["months"])]
    lines.append("  own machine: %s a month for the price%s, %g hours a month" % (
        money(result["own_fixed"]),
        " + %s electricity" % money(result["power"]) if result["power"] is not None else " (electricity not counted: give --watts)",
        result["hours"]))
    if result["rent"] is not None:
        lines.append("  renting the same hours (%s): %s a month" % (result["rent_row"], money(result["rent"])))
        if result["break_even_share"] is not None:
            lines.append("  the own machine costs less once it works more than %d%% of the month's hours" % (
                round(result["break_even_share"] * 100)))
    return "\n".join(lines)


# ---------------------------------------------------------------- main ----------------------

def parser():
    top = argparse.ArgumentParser(prog="billcall.py", description="What a company's AI costs. Buys nothing.")
    top.add_argument("--prices", help="price table (default: data/prices.json of this plugin)")
    sub = top.add_subparsers(dest="command")

    one = sub.add_parser("estimate", help="the monthly bill of a mix of seats and API work")
    one.add_argument("profile")
    one.add_argument("--policy", help="company-ai-policy.json (default: looked for where firmcall puts it)")
    one.add_argument("--json", action="store_true")
    one.add_argument("--today", help="YYYY-MM-DD (default: today)")

    one = sub.add_parser("use", help="may this row carry scripts, or this many people")
    one.add_argument("--product", required=True)
    one.add_argument("--for", dest="purpose", required=True, choices=("scripts", "people"))
    one.add_argument("--people", type=int, default=1)
    one.add_argument("--policy", help="company-ai-policy.json (default: looked for where firmcall puts it)")

    one = sub.add_parser("week", help="what was spent")
    one.add_argument("--days", type=int, default=7)
    one.add_argument("--logs", action="append", help="a Claude Code projects folder (repeatable)")
    one.add_argument("--team-csv", help="the spend report CSV of a Team or Enterprise organisation")
    one.add_argument("--roster", help="one e-mail per line: the people who hold seats")
    one.add_argument("--idle-below", default=1, type=float, help="requests below which a seat counts as idle")
    one.add_argument("--otel", help="an OTLP JSON export holding claude_code.cost.usage")
    one.add_argument("--report", help="write the report into this folder, under the language's file name")
    one.add_argument("--lang", help="en, es, pt, ru or uk (default: the policy's, then the system's)")
    one.add_argument("--json", action="store_true")

    one = sub.add_parser("guard", help="spend against a budget: 50, 80, 100 per cent")
    one.add_argument("--budget-day", type=float)
    one.add_argument("--budget-month", type=float)
    one.add_argument("--policy", help="company-ai-policy.json (default: looked for where firmcall puts it)")
    one.add_argument("--logs", action="append")
    one.add_argument("--hook", action="store_true", help="SessionStart: one line from 50%%, silence below")
    one.add_argument("--lang", help="en, es, pt, ru or uk (default: the company policy, then the system)")

    one = sub.add_parser("contract", help="Team against Enterprise; the modelPricing block")
    one.add_argument("--people", type=int)
    one.add_argument("--usage", type=float, help="API use a person a month on Enterprise, USD")
    one.add_argument("--billing", default="annual", choices=("annual", "monthly"))
    one.add_argument("--discount", type=float, help="a flat discount in per cent: 15 -> multiplier 0.85")
    one.add_argument("--rates", help="a JSON file with multiplier and/or overrides")
    one.add_argument("--target", default="managed", choices=("managed", "user", "project", "local", "settings-flag"))
    one.add_argument("--out", help="write the block to this file")

    one = sub.add_parser("local", help="an own machine against renting")
    one.add_argument("--hardware", required=True)
    one.add_argument("--rent")
    one.add_argument("--watts", type=float)
    one.add_argument("--hours-per-day", type=float, default=24.0)
    one.add_argument("--months", type=int, default=36)

    one = sub.add_parser("side-by-side", help="the US five, the Chinese vendors or local machines, side by side")
    one.add_argument("group", choices=GROUPS)
    one.add_argument("--lang", help="language of the cheap-class headline under the china table (default: en)")

    one = sub.add_parser("prices", help="rows of the price table")
    one.add_argument("--kind")
    one.add_argument("--use")
    one.add_argument("--vendor")
    return top


def run(argv):
    args = parser().parse_args(argv)
    if not args.command:
        parser().print_help()
        return 2
    table = load_prices(args.prices)
    if args.command == "estimate":
        today = parse_day(args.today) if args.today else datetime.date.today()
        if today is None:
            raise Problem("--today needs YYYY-MM-DD")
        policy, _where = read_policy(args.policy)
        result = estimate(load_json(args.profile, "the profile"), table, today, policy)
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(say_estimate(result, table))
        return 1 if result["broken"] else 0
    if args.command == "use":
        if args.people < 1:
            raise Problem("--people must be 1 or more")
        policy, _where = read_policy(args.policy)
        broken = use_check(table, args.product, args.purpose, args.people, policy)
        row = table["rows"][args.product]
        if broken:
            for rule in broken:
                print("BROKEN: " + rule)
            return 1
        print("billcall: %s (%s, use: %s) may carry %s" % (
            args.product, row.get("product"), row.get("use"),
            "scripts" if args.purpose == "scripts" else "%d %s" % (args.people, "person" if args.people == 1 else "people")))
        return 0
    if args.command == "week":
        result = week(args, table)
        policy = {}
        try:
            policy = read_policy()[0]
        except Problem:
            policy = {}
        code = pick_lang(args.lang, policy)
        words = lang_words(code)
        text = say_week(result, words)
        if args.report:
            if not os.path.isdir(args.report):
                raise Problem("--report needs an existing folder, got %s" % args.report)
            target = os.path.join(args.report, words.get("week_file", "week-AI.md"))
            with open(target, "w", encoding="utf-8") as handle:
                handle.write(text)
            print("billcall: the report is in %s" % target)
        if args.json:
            logs = dict(result["logs"])
            logs["models"] = dict((k, v) for k, v in logs["models"].items())
            team = result["team"]
            if team is not None:
                team = dict((k, dict(v, models=sorted(v["models"]), products=sorted(v["products"])))
                            for k, v in team.items())
            print(json.dumps(dict(result, logs=logs, team=team), indent=2, sort_keys=True, default=str))
        elif not args.report:
            print(text)
        return 0
    if args.command == "guard":
        try:
            result = guard(args, table)
        except Problem:
            if args.hook:
                return 0
            raise
        if result is None:
            if not args.hook:
                print("billcall guard: no budget set (--budget-day/--budget-month, max_usd_per_day or "
                      "max_usd_per_month in company-ai-policy.json, or ~/.billcall/budget.json)")
            return 0
        worst, head = say_guard(result, lang_words(result.get("lang", "en")))
        if args.hook:
            if worst >= 50:
                print(head)
            return 0
        print(head)
        return 1 if worst >= 100 else 0
    if args.command == "contract":
        if args.people is not None:
            if args.people <= 0 or args.usage is None or args.usage < 0:
                raise Problem("--people needs a number above 0 and --usage the API use a person a month")
            print(say_seats(seats_against_enterprise(table, args.people, args.usage, args.billing)))
            return 0
        if args.target != "managed":
            print("billcall contract: " + TARGET_REFUSAL)
            return 1
        if args.discount is not None:
            if not 0 <= args.discount < 100:
                raise Problem("--discount is a per cent from 0 to below 100")
            rates = {"multiplier": round(1 - args.discount / 100.0, 6)}
        elif args.rates:
            rates = load_json(args.rates, "the contract rates")
        else:
            raise Problem("contract needs --people and --usage, --discount, or --rates")
        block = model_pricing(rates)
        text = json.dumps(block, indent=2, sort_keys=True)
        if args.out:
            with open(args.out, "w", encoding="utf-8") as handle:
                handle.write(text + "\n")
            print("billcall: the modelPricing block is in %s - deploy it as managed settings" % args.out)
        else:
            print(text)
        note = "managed settings only; Claude Code 2.1.242 or later"
        if block["modelPricing"].get("multiplier", 1) > 1:
            note += "; a markup needs 2.1.271 or later"
        print("# " + note + " (code.claude.com/docs/en/costs, read 2026-10-02)", file=sys.stderr)
        return 0
    if args.command == "local":
        if args.watts is not None and args.watts < 0:
            raise Problem("--watts must be 0 or more")
        print(say_local(local_payback(table, args.hardware, args.rent, args.watts, args.hours_per_day, args.months)))
        return 0
    if args.command == "side-by-side":
        header, lines = side_by_side(table, args.group)
        print("billcall side-by-side %s - price table of %s; every figure names the day it was read and its page"
              % (args.group, table.get("updated")))
        print(say_side_by_side(header, lines))
        if args.group == "china":
            print("")
            print("Cheap class: how many times cheaper per 1M tokens (list price, 3 input : 1 output)")
            print(say_cheap_class(cheap_class(table), table, lang_words(pick_lang(args.lang or "en"))))
        print("billcall counts and compares; it buys nothing - the person responsible buys.")
        return 0
    if args.command == "prices":
        for row_id in sorted(table["rows"]):
            row = table["rows"][row_id]
            if args.kind and row.get("kind") != args.kind:
                continue
            if args.use and row.get("use") != args.use:
                continue
            if args.vendor and args.vendor.lower() not in str(row.get("vendor", "")).lower():
                continue
            first = (row.get("prices") or [{}])[0] if row.get("prices") else {}
            print("%s | %s | %s | %s %s | use %s | %s, read %s" % (
                row_id, row.get("product"), row.get("kind"),
                money(float(first["amount"]), row.get("currency", "USD")) if first else "no price",
                first.get("unit", "") if first else row.get("price_note", ""), row.get("use"),
                row.get("status"), row.get("checked")))
        return 0
    return 2


def main(argv=None):
    try:
        return run(sys.argv[1:] if argv is None else argv)
    except Problem as exc:
        print("billcall: %s" % exc)
        return 2
    except SystemExit as exc:          # argparse's own exits (--help, a bad flag)
        return exc.code if isinstance(exc.code, int) else 2
    except Exception as exc:           # a bug here must say so in one line, never a traceback
        print("billcall: internal error: %r" % (exc,))
        return 2


if __name__ == "__main__":
    sys.exit(main())
