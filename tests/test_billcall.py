"""billcall: the examples add up, every rule of the price table reddens when it is broken, the logs are
counted once per response, the budget line speaks at its marks and only there.

Each rule has a green twin next to its red case, so a check that cannot fail shows up as a failing test.
"""
import contextlib
import datetime
import importlib.util
import io
import json
import os
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SCRIPT = ROOT / "skills" / "billcall" / "scripts" / "billcall.py"
PRICES = ROOT / "data" / "prices.json"
EXAMPLES = ROOT / "data" / "examples"
DAY = datetime.date(2026, 10, 2)


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


billcall = load("billcall", SCRIPT)
sys.path.insert(0, str(ROOT / "tools"))
check_prices = load("check_prices", ROOT / "tools" / "check_prices.py")
mutations = load("billcall_mutations", ROOT / "tools" / "mutations.py")
TABLE = billcall.load_prices(str(PRICES))


def example(name):
    return json.loads((EXAMPLES / name).read_text(encoding="utf-8"))


def cli(*args):
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = billcall.main(list(args))
    return code, out.getvalue()


def profile(**parts):
    base = {"schema": 1, "name": "test", "billing": "annual", "roles": [], "automated": []}
    base.update(parts)
    return base


class Examples(unittest.TestCase):
    def test_agency_of_ten(self):
        result = billcall.estimate(example("agency-10.json"), TABLE, DAY)
        self.assertEqual(result["broken"], [])
        self.assertAlmostEqual(result["new"]["USD"], 455.0, places=6)
        self.assertEqual(result["paid"], {})
        self.assertIn("builders: 3 x Claude Team - Premium seat, billed annually $100 = $300 a month", result["lines"])
        self.assertIn("everyone-else: 7 x Claude Team - Standard seat, billed annually $20 = $140 a month",
                      result["lines"])
        self.assertAlmostEqual(result["per_task"], 455.0 / 480.0, places=6)
        self.assertAlmostEqual(result["alternatives"][0]["new"]["USD"], 215.0, places=6)
        self.assertTrue(any("on the API instead" in note and "$13" in note for note in result["notes"]))

    def test_firm_of_a_hundred_with_copilot_already_paid(self):
        result = billcall.estimate(example("firm-100.json"), TABLE, DAY)
        self.assertEqual(result["broken"], [])
        # 20 Premium seats + DeepSeek flash off-peak (12.72) + Sonnet 5.5 in batch with the newer tokenizer (39)
        self.assertAlmostEqual(result["new"]["USD"], 2000 + 12.72 + 39, places=6)
        self.assertAlmostEqual(result["paid"]["USD"], 80 * 18, places=6)
        whole = result["alternatives"][0]
        self.assertAlmostEqual(whole["new"]["USD"], 2000 + 1600 + 12.72 + 39, places=6)
        self.assertEqual(whole["paid"], {})
        self.assertTrue(any("holds until 2026-12-31" in note for note in result["notes"]), result["notes"])

    def test_seat_figures_of_the_plan(self):
        """Plan section 0.2: 10 on Team Standard $200; 3 Premium + 7 Standard $440; 100 people 20 Premium +
        80 Standard $3,600, or $3,440 with the 80 on Copilot Business."""
        cases = (([("a", 10, "anthropic-team-standard", False)], 200),
                 ([("a", 3, "anthropic-team-premium", False), ("b", 7, "anthropic-team-standard", False)], 440),
                 ([("a", 20, "anthropic-team-premium", False), ("b", 80, "anthropic-team-standard", False)], 3600),
                 ([("a", 20, "anthropic-team-premium", False),
                   ("b", 80, "microsoft-365-copilot-business", True)], 3440))
        for roles, total in cases:
            result = billcall.estimate(profile(roles=[
                {"id": rid, "people": people, "product": product, "already_paid": paid}
                for rid, people, product, paid in roles]), TABLE, DAY)
            self.assertEqual(result["broken"], [])
            whole = result["new"].get("USD", 0) + result["paid"].get("USD", 0)
            self.assertAlmostEqual(whole, total, places=6, msg=roles)



class SideBySide(unittest.TestCase):
    """The owner's three tables come from the price table itself: every figure with its day and its page."""

    def test_the_us_five_stand_side_by_side_with_a_day_and_a_page_in_every_filled_cell(self):
        header, lines = billcall.side_by_side(TABLE, "us")
        self.assertEqual([line[0] for line in lines][:5], list(billcall.US_FIVE))
        for line in lines:
            self.assertEqual(len(line), len(header))
            for cell in line[1:]:
                if cell != "-":
                    self.assertRegex(cell, r"read \[\d{4}-\d{2}-\d{2}\]\(https://")
                self.assertNotIn("|", cell)

    def test_the_chinese_vendors_and_the_local_machines(self):
        _header, lines = billcall.side_by_side(TABLE, "china")
        self.assertTrue({"DeepSeek", "Moonshot AI", "Alibaba Cloud", "Z.ai"} <= set(line[0] for line in lines))
        _header, lines = billcall.side_by_side(TABLE, "local")
        self.assertTrue(any("DGX Spark" in line[0] for line in lines))
        self.assertTrue(any("H100" in line[0] for line in lines))

    def test_a_changed_price_shows_in_the_table(self):
        table = json.loads(PRICES.read_text(encoding="utf-8"))
        for row in table["rows"]:
            if row["id"] == "anthropic-team-standard":
                row["prices"][0]["amount"] = 21
        path = Path(tempfile.mkdtemp()) / "prices.json"
        path.write_text(json.dumps(table), encoding="utf-8")
        _header, lines = billcall.side_by_side(billcall.load_prices(str(path)), "us")
        self.assertIn("$21 a seat a month", lines[0][1])

    def test_on_the_command_line(self):
        code, out = cli("side-by-side", "china")
        self.assertEqual(code, 0)
        self.assertIn("| Vendor | API", out)
        self.assertIn("buys nothing", out)


class QualityAndSurfaces(unittest.TestCase):
    """estimate weighs a cheaper model by its quality index, and a person who works in an agent all day on
    the lowest seat tier is told the shared usage pool runs out first (plan section 0.7: quality_index,
    the Cowork factor)."""

    def test_a_cheaper_and_weaker_model_is_named_as_weaker(self):
        result = billcall.estimate(profile(
            automated=[{"id": "night", "product": "anthropic-api-claude-sonnet-5-5", "input_mtok": 10, "output_mtok": 1}],
            alternatives=[{"name": "cheap", "replace": {"night": {"product": "deepseek-api-flash"}}}]), TABLE, DAY)
        change = " ".join(result["alternatives"][0]["quality"])
        self.assertIn("anthropic-api-claude-sonnet-5-5 -> deepseek-api-flash", change)
        self.assertIn("(lower)", change)
        self.assertTrue(any("quality index" in line for line in result["lines"]))

    def test_a_stronger_model_is_named_as_stronger_and_an_unknown_index_asks_for_a_bench(self):
        result = billcall.estimate(example("agency-10.json"), TABLE, DAY)
        self.assertIn("(higher)", " ".join(result["alternatives"][1]["quality"]))
        unknown = billcall.estimate(profile(
            automated=[{"id": "n", "product": "anthropic-api-claude-haiku-4-5", "input_mtok": 1}],
            alternatives=[{"name": "x", "replace": {"n": {"product": "deepseek-api-v4-pro", "modifiers": []}}},
                          {"name": "y", "replace": {"n": {"product": "zai-api-glm-4-7-flash"}}}]), TABLE, DAY)
        self.assertNotIn("bench", " ".join(unknown["alternatives"][0]["quality"]))
        self.assertIn("run a bench", " ".join(unknown["alternatives"][1]["quality"]))

    def test_an_unchanged_model_says_nothing_about_quality(self):
        result = billcall.estimate(example("agency-10.json"), TABLE, DAY)
        self.assertEqual(result["alternatives"][0]["quality"], [])

    def test_cowork_on_a_standard_seat_points_to_premium(self):
        table = dict(TABLE, constants=dict(TABLE["constants"], **{billcall.COWORK: {
            "id": billcall.COWORK, "value": 5, "high": 20, "url": "https://example.test/cowork"}}))
        notes = billcall.estimate(profile(roles=[{"id": "ops", "people": 4, "product": "anthropic-team-standard",
                                                  "surface": "cowork"}]), table, DAY)["notes"]
        said = " ".join(notes)
        self.assertIn("ops: 4 people work in Cowork", said)
        self.assertIn("5-20x the usage of a chat", said)
        self.assertIn("Premium seat gives 5x", said)

    def test_no_surface_no_note(self):
        notes = billcall.estimate(profile(roles=[{"id": "ops", "people": 4, "product": "anthropic-team-standard"}]),
                                  TABLE, DAY)["notes"]
        self.assertFalse(any("work in Cowork" in note for note in notes))


class Rules(unittest.TestCase):
    def broken(self, policy=None, **parts):
        return billcall.estimate(profile(**parts), TABLE, DAY, policy)["broken"]

    def test_a_personal_plan_for_a_company_is_broken_and_for_one_person_is_kept(self):
        self.assertTrue(any("personal plan" in rule for rule in self.broken(
            roles=[{"id": "all", "people": 5, "product": "anthropic-claude-max-5x"}])))
        self.assertEqual(self.broken(roles=[{"id": "me", "people": 1, "product": "anthropic-claude-pro"}]), [])

    def test_scripts_on_a_coding_or_team_plan_are_broken_and_on_the_api_are_kept(self):
        for product in ("zai-glm-coding-plan", "alibaba-token-plan-team", "anthropic-team-standard"):
            rules = self.broken(automated=[{"id": "job", "product": product, "input_mtok": 1}])
            self.assertTrue(any("use: automated" in rule for rule in rules), (product, rules))
        self.assertEqual(self.broken(automated=[{"id": "job", "product": "anthropic-api-claude-haiku-4-5",
                                                 "input_mtok": 1}]), [])

    def test_seat_minimum_and_maximum(self):
        self.assertTrue(any("sold from 20 seats" in rule for rule in self.broken(
            roles=[{"id": "x", "people": 5, "product": "anthropic-enterprise"}])))
        self.assertTrue(any("sold from 5 seats" in rule for rule in self.broken(
            roles=[{"id": "x", "people": 3, "product": "byteplus-coding-plan-team"}])))
        self.assertTrue(any("up to 150 seats" in rule for rule in self.broken(
            roles=[{"id": "a", "people": 100, "product": "anthropic-team-standard"},
                   {"id": "b", "people": 60, "product": "anthropic-team-premium"}])))

    def test_tiers_of_one_plan_share_its_seat_limits(self):
        """One Premium seat beside nine Standard ones is a Team of ten, not a Premium plan of one."""
        self.assertEqual(self.broken(roles=[{"id": "a", "people": 1, "product": "anthropic-team-premium"},
                                            {"id": "b", "people": 9, "product": "anthropic-team-standard"}]), [])

    def test_a_machine_is_not_monthly_work_and_an_unknown_product_is_named(self):
        self.assertTrue(any("is a machine" in rule for rule in self.broken(
            automated=[{"id": "box", "product": "nvidia-dgx-spark"}])))
        self.assertTrue(any("no row" in rule for rule in self.broken(
            roles=[{"id": "x", "people": 2, "product": "no-such-product"}])))

    def test_a_row_without_a_price_says_so_instead_of_counting_zero(self):
        rules = self.broken(roles=[{"id": "x", "people": 30, "product": "openai-chatgpt-enterprise"}])
        self.assertTrue(any("no published price" in rule for rule in rules), rules)

    def test_the_company_policy_names_the_vendors(self):
        """allowed_providers and deny_providers of company-ai-policy.json: red outside them, green inside."""
        work = [{"id": "triage", "product": "deepseek-api-flash", "input_mtok": 1}]
        self.assertTrue(any("denies DeepSeek" in rule for rule in self.broken(
            policy={"deny_providers": ["deepseek"]}, automated=work)))
        self.assertTrue(any("allows only anthropic" in rule for rule in self.broken(
            policy={"allowed_providers": ["Anthropic"]}, automated=work)))
        self.assertEqual(self.broken(policy={"allowed_providers": ["DeepSeek", "Anthropic"]}, automated=work), [])
        self.assertEqual(self.broken(policy={}, automated=work), [])
        with self.assertRaises(billcall.Problem):
            self.broken(policy={"deny_providers": "deepseek"}, automated=work)


class Cli(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.empty_policy = Path(self.tmp.name) / "company-ai-policy.json"
        self.empty_policy.write_text("{}", encoding="utf-8")

    def tearDown(self):
        self.tmp.cleanup()

    def test_exit_codes(self):
        code, out = cli("estimate", str(EXAMPLES / "agency-10.json"), "--today", "2026-10-02",
                        "--policy", str(self.empty_policy))
        self.assertEqual(code, 0, out)
        self.assertIn("new money a month: $455", out)
        self.assertIn("buys nothing", out)
        bad = Path(self.tmp.name) / "bad.json"
        bad.write_text(json.dumps(profile(roles=[{"id": "x", "people": 4, "product": "anthropic-claude-pro"}])))
        code, out = cli("estimate", str(bad), "--today", "2026-10-02", "--policy", str(self.empty_policy))
        self.assertEqual(code, 1, out)
        self.assertIn("BROKEN:", out)
        code, out = cli("estimate", str(Path(self.tmp.name) / "missing.json"), "--policy", str(self.empty_policy))
        self.assertEqual(code, 2)
        self.assertIn("billcall: cannot read", out)

    def test_use_is_the_check_before_scripts_run_on_a_plan(self):
        """routecall's crew asks this before it starts another program on a plan: a coding plan, a team token plan
        or a personal plan for scripts is red, an API row is green."""
        policy = str(self.empty_policy)
        for product in ("zai-glm-coding-plan", "alibaba-token-plan-team", "anthropic-claude-max-5x"):
            code, out = cli("use", "--product", product, "--for", "scripts", "--policy", policy)
            self.assertEqual(code, 1, (product, out))
            self.assertIn("use: automated", out)
        code, out = cli("use", "--product", "anthropic-api-claude-haiku-4-5", "--for", "scripts", "--policy", policy)
        self.assertEqual(code, 0, out)
        self.assertIn("may carry scripts", out)
        code, out = cli("use", "--product", "anthropic-claude-pro", "--for", "people", "--people", "6", "--policy", policy)
        self.assertEqual(code, 1, out)
        self.assertIn("personal plan", out)
        code, out = cli("use", "--product", "anthropic-team-standard", "--for", "people", "--people", "6",
                        "--policy", policy)
        self.assertEqual(code, 0, out)
        # Enterprise is sold from 20 seats: five people are not a seat count it can carry
        code, out = cli("use", "--product", "anthropic-enterprise", "--for", "people", "--people", "5",
                        "--policy", policy)
        self.assertEqual(code, 1, out)
        self.assertIn("sold from 20 seats", out)
        code, out = cli("use", "--product", "anthropic-enterprise", "--for", "people", "--people", "20",
                        "--policy", policy)
        self.assertEqual(code, 0, out)
        code, out = cli("use", "--product", "no-such-row", "--for", "scripts", "--policy", policy)
        self.assertEqual(code, 2, out)

    def test_help_is_argparse_usage(self):
        code, out = cli("--help")
        self.assertEqual(code, 0)
        self.assertIn("usage:", out)

    def test_prices_lists_rows(self):
        code, out = cli("prices", "--vendor", "Anthropic", "--kind", "seat")
        self.assertEqual(code, 0)
        self.assertIn("anthropic-team-premium", out)
        self.assertNotIn("openai", out)


def log_line(msg_id, request, model, usage, stamp):
    return json.dumps({"type": "assistant", "requestId": request, "timestamp": stamp,
                       "message": {"id": msg_id, "model": model, "usage": usage}})


class Logs(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name) / "projects"
        (self.root / "proj").mkdir(parents=True)

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, lines):
        (self.root / "proj" / "session.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")

    def test_one_response_on_several_lines_counts_once(self):
        opus = {"input_tokens": 1000, "output_tokens": 2000, "cache_read_input_tokens": 10000,
                "cache_creation_input_tokens": 4000,
                "cache_creation": {"ephemeral_1h_input_tokens": 4000, "ephemeral_5m_input_tokens": 0}}
        stamp = "2026-10-01T10:00:00.000Z"
        self.write([log_line("msg_1", "req_1", "claude-opus-5-5", opus, stamp),
                    log_line("msg_1", "req_1", "claude-opus-5-5", opus, stamp),
                    log_line("msg_2", "req_2", "claude-haiku-4-5-20251001", {"input_tokens": 500000}, stamp),
                    log_line("msg_3", "req_3", "claude-sonnet-4-20250514", {"input_tokens": 100}, stamp),
                    "not json at all",
                    json.dumps({"type": "user", "message": {"content": "no usage here"}})])
        entries, files, partial = billcall.read_usage([str(self.root)])
        self.assertEqual((len(entries), files, partial), (3, 1, False))
        total, models, _days = billcall.spend(entries, TABLE["rows"])
        # Opus 5.5: 1000 x $4 + 2000 x $20 + 10000 x $0.20 + 4000 one-hour writes x $8; Haiku: 500000 x $1
        self.assertAlmostEqual(total, 0.078 + 0.5, places=9)
        self.assertIsNone(models["claude-sonnet-4-20250514"][1])     # named, never guessed
        self.assertEqual(models["claude-sonnet-4-20250514"][0], 100)

    def test_model_ids_find_their_rows(self):
        rows = TABLE["rows"]
        self.assertEqual(billcall.model_row("claude-opus-5-5-20260901", rows)["id"], "anthropic-api-claude-opus-5-5")
        self.assertEqual(billcall.model_row("claude-opus-5-5[1m]", rows)["id"], "anthropic-api-claude-opus-5-5")
        self.assertIsNone(billcall.model_row("claude-opus-5-50", rows))
        old = {"anthropic-api-claude-opus-5": {"id": "anthropic-api-claude-opus-5"}}
        self.assertEqual(billcall.model_row("claude-opus-5-20260301", old)["id"], "anthropic-api-claude-opus-5")
        self.assertIsNone(billcall.model_row("claude-opus-5-6", old))     # another version, never the old price
        self.assertIsNone(billcall.model_row("claude-opus-5-6-20261101", old))
        self.assertIsNone(billcall.model_row("claude-opus-5", {}))

    def test_team_csv_roster_and_idle_seats(self):
        csv_path = Path(self.tmp.name) / "spend.csv"
        csv_path.write_text(
            "User's email,Account UUID,Product,Model,total_requests,total_prompt_tokens,total_completion_tokens,"
            "total_net_spend_usd,total_gross_spend_usd\n"
            "ann@acme.test,u1,Claude Code,claude-opus-5-5,120,1000000,200000,$12.50,14.00\n"
            "ann@acme.test,u1,Chat,claude-sonnet-5-5,30,100000,20000,1.00,1.10\n"
            "bob@acme.test,u2,Cowork,claude-sonnet-5-5,0,0,0,0,0\n", encoding="utf-8")
        team = billcall.team_csv(str(csv_path))
        self.assertAlmostEqual(team["ann@acme.test"]["net"], 13.5)
        self.assertEqual(team["ann@acme.test"]["requests"], 150)
        roster = Path(self.tmp.name) / "roster.txt"
        roster.write_text("# seats\nann@acme.test\nBob@acme.test\ncyd@acme.test\n", encoding="utf-8")
        code, out = cli("week", "--logs", str(self.root), "--team-csv", str(csv_path), "--roster", str(roster),
                        "--lang", "en")
        self.assertEqual(code, 0, out)
        idle = out.split("## Seats nobody used", 1)[1]
        self.assertIn("bob@acme.test", idle)
        self.assertIn("cyd@acme.test", idle)
        self.assertNotIn("ann@acme.test", idle)

    def test_a_roster_without_a_spend_report_is_refused(self):
        roster = Path(self.tmp.name) / "roster.txt"
        roster.write_text("ann@acme.test\n", encoding="utf-8")
        code, out = cli("week", "--logs", str(self.root), "--roster", str(roster))
        self.assertEqual(code, 2)
        self.assertIn("--team-csv", out)

    def test_otel_cumulative_series_count_once_and_deltas_add(self):
        def export(value, temporality, email, start):
            return json.dumps({"resourceMetrics": [{"scopeMetrics": [{"metrics": [{
                "name": "claude_code.cost.usage",
                "sum": {"aggregationTemporality": temporality, "dataPoints": [{
                    "asDouble": value, "startTimeUnixNano": start,
                    "attributes": [{"key": "user.email", "value": {"stringValue": email}},
                                   {"key": "model", "value": {"stringValue": "claude-opus-5-5"}}]}]}}]}]}]})
        path = Path(self.tmp.name) / "otel.jsonl"
        path.write_text("\n".join([export(0.5, 2, "a@x.test", "1"), export(1.25, 2, "a@x.test", "1"),
                                   export(0.1, 1, "b@x.test", "2"), export(0.2, 1, "b@x.test", "2")]) + "\n",
                        encoding="utf-8")
        people = billcall.otel_cost(str(path))
        self.assertAlmostEqual(people["a@x.test"]["claude-opus-5-5"], 1.25)
        self.assertAlmostEqual(people["b@x.test"]["claude-opus-5-5"], 0.3)

    def test_the_report_is_named_in_the_persons_language(self):
        words = json.loads((ROOT / "lang" / "ru.json").read_text(encoding="utf-8"))
        out_dir = Path(self.tmp.name) / "company"
        out_dir.mkdir()
        code, out = cli("week", "--logs", str(self.root), "--report", str(out_dir), "--lang", "ru")
        self.assertEqual(code, 0, out)
        report = out_dir / words["week_file"]
        self.assertTrue(report.is_file(), out)
        self.assertIn(words["week_title"], report.read_text(encoding="utf-8"))

    def test_every_language_names_every_report_word(self):
        keys = set(k for k in json.loads((ROOT / "lang" / "en.json").read_text(encoding="utf-8")) if not k.startswith("_"))
        for code in ("en", "es", "pt", "ru", "uk"):
            words = json.loads((ROOT / "lang" / ("%s.json" % code)).read_text(encoding="utf-8"))
            self.assertEqual(set(k for k in words if not k.startswith("_")), keys, code)


@unittest.skipIf(os.path.exists("/Library/Application Support/ClaudeCode/company-ai-policy.json")
                 or os.path.exists("/etc/claude-code/company-ai-policy.json"),
                 "this machine has a managed company policy, which outranks the test's own")
class Guard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.home = base / "home"
        self.home.mkdir()
        self.logs = base / "projects"
        (self.logs / "p").mkdir(parents=True)
        now = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        # Haiku 4.5 at $1 per million input tokens: 850,000 tokens spent now = $0.85
        (self.logs / "p" / "s.jsonl").write_text(
            log_line("m1", "r1", "claude-haiku-4-5", {"input_tokens": 850000}, now) + "\n", encoding="utf-8")
        self.policy = base / "company-ai-policy.json"

    def tearDown(self):
        self.tmp.cleanup()

    def run_guard(self, *extra, budget=None):
        env = dict(os.environ, HOME=str(self.home), USERPROFILE=str(self.home))
        env.pop("CLAUDE_CONFIG_DIR", None)
        env.pop("COMPANY_AI_POLICY", None)
        env["BILLCALL_LANG"] = "en"
        if budget is not None:
            self.policy.write_text(json.dumps({"schema": 1, "max_usd_per_day": budget}), encoding="utf-8")
            env["COMPANY_AI_POLICY"] = str(self.policy)
        return subprocess.run([sys.executable, str(SCRIPT), "guard", "--logs", str(self.logs)] + list(extra),
                              input=json.dumps({"cwd": str(self.home)}), capture_output=True, text=True,
                              env=env, cwd=str(self.home), timeout=60)

    def test_levels(self):
        self.assertEqual([billcall.level(s, 100) for s in (0, 49.9, 50, 79, 80, 99, 100, 250)],
                         [0, 0, 50, 50, 80, 80, 100, 100])
        self.assertIsNone(billcall.level(10, None))

    def test_the_hook_is_silent_without_a_budget(self):
        done = self.run_guard("--hook")
        self.assertEqual((done.returncode, done.stdout), (0, ""), done.stderr)

    def test_the_hook_speaks_from_half_the_budget(self):
        done = self.run_guard("--hook", budget=1.0)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("80% of the budget is gone", done.stdout)
        self.assertIn("$0.85", done.stdout)
        quiet = self.run_guard("--hook", budget=10.0)       # 8.5%: below half, nothing to say
        self.assertEqual((quiet.returncode, quiet.stdout), (0, ""), quiet.stderr)

    def test_the_budget_line_speaks_the_persons_language(self):
        words = json.loads((ROOT / "lang" / "ru.json").read_text(encoding="utf-8"))
        done = self.run_guard("--hook", "--lang", "ru", budget=1.0)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn(words["guard_80"].strip(". "), done.stdout)
        self.assertNotIn("of the budget", done.stdout)

    def test_a_spent_budget_is_red_on_the_command_line(self):
        done = self.run_guard(budget=0.5)
        self.assertEqual(done.returncode, 1, done.stdout + done.stderr)
        self.assertIn("The budget is spent", done.stdout)
        green = self.run_guard(budget=1.0)
        self.assertEqual(green.returncode, 0, green.stdout + green.stderr)


class Contract(unittest.TestCase):
    def test_team_against_enterprise(self):
        result = billcall.seats_against_enterprise(TABLE, 40, 60)
        self.assertAlmostEqual(result["premium"], 4000)
        self.assertAlmostEqual(result["enterprise"], 40 * 20 + 40 * 60)
        self.assertAlmostEqual(result["break_even_usage"], 80)       # plan section 0.2: the $80 threshold
        small = billcall.seats_against_enterprise(TABLE, 10, 0)
        self.assertEqual(small["enterprise_seats"], 20)             # Enterprise is sold from 20 seats
        self.assertAlmostEqual(small["break_even_usage"], 60)

    def test_model_pricing_keeps_the_documented_shape(self):
        self.assertEqual(billcall.model_pricing({"multiplier": 0.85}), {"modelPricing": {"multiplier": 0.85}})
        rates = {"input": 3.4, "output": 17, "cacheRead": 0.17, "cacheWrite": 4.25}
        self.assertEqual(billcall.model_pricing({"overrides": {"claude-opus-5-5": rates}}),
                         {"modelPricing": {"overrides": {"claude-opus-5-5": rates}}})
        for bad in ({"multiplier": 0}, {"multiplier": 11}, {"multiplier": True}, {},
                    {"overrides": {"claude-opus-5-5": {"input": 1, "output": 2, "cacheRead": 0.1}}},
                    {"overrides": {"m": dict(rates, input=10001)}}, {"multiplier": 1, "discount": 5}):
            with self.assertRaises(billcall.Problem, msg=bad):
                billcall.model_pricing(bad)

    def test_only_managed_settings(self):
        code, out = cli("contract", "--discount", "15")
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out), {"modelPricing": {"multiplier": 0.85}})
        code, out = cli("contract", "--discount", "15", "--target", "user")
        self.assertEqual(code, 1)
        self.assertIn("only from managed settings", out)


class Local(unittest.TestCase):
    def test_own_card_against_renting_breaks_even_near_a_third_of_the_month(self):
        """Plan section 0.4: an own card beats renting above roughly 30-40% of the month's hours."""
        result = billcall.local_payback(TABLE, "nvidia-rtx-pro-6000-96gb", "runpod-rtx-pro-6000", watts=600)
        self.assertAlmostEqual(result["own_fixed"], 16000 / 36.0, places=6)
        self.assertAlmostEqual(result["power"], 0.6 * 730 * 0.1453, places=6)
        self.assertAlmostEqual(result["break_even_share"], 0.304, places=3)
        self.assertIsNone(billcall.local_payback(TABLE, "apple-mac-mini-m6")["power"])  # no watts: not counted
        with self.assertRaises(billcall.Problem):
            billcall.local_payback(TABLE, "runpod-h100-sxm")


class PriceTable(unittest.TestCase):
    def test_the_table_is_clean_on_the_day_it_was_read(self):
        doc, error = check_prices.load(str(PRICES))
        self.assertIsNone(error)
        day = check_prices.parse_date(doc["updated"])
        self.assertEqual(check_prices.check_document(doc, day, strict=True), [])

    def test_the_models_field_of_a_box_is_checked(self):
        doc, _error = check_prices.load(str(PRICES))
        day = check_prices.parse_date(doc["updated"])
        box = next(r for r in doc["rows"] if r["kind"] == "hardware")
        good = {"lead": [{"model": "m", "quant": "q4", "license": "MIT", "url": "https://huggingface.co/x/m"}],
                "on_call": []}
        cases = ((good, False),
                 ({"lead": []}, True),
                 ({"lead": [dict(good["lead"][0], url="http://huggingface.co/x/m")]}, True),
                 ({"lead": [dict(good["lead"][0], license="")]}, True),
                 ({"lead": good["lead"], "extra": []}, True))
        for models, red in cases:
            row = dict(box, models=models)
            problems = check_prices.check_document(dict(doc, rows=[row]), day, strict=False)
            self.assertEqual(bool(problems), red, models)
        api = next(r for r in doc["rows"] if r["kind"] == "api")
        self.assertTrue(check_prices.check_document(dict(doc, rows=[dict(api, models=good)]), day))

    def test_a_constant_range_top_is_checked(self):
        doc, _error = check_prices.load(str(PRICES))
        day = check_prices.parse_date(doc["updated"])
        base = dict(doc["constants"][0], value=5)
        for high, red in ((20, False), (5, False), (4, True), ("20", True)):
            problems = check_prices.check_document(dict(doc, constants=[dict(base, high=high)]), day)
            self.assertEqual(bool(problems), red, high)

    def test_the_api_day_constant_is_there(self):
        self.assertEqual(TABLE["constants"][billcall.API_DAY]["value"], 13)

    def test_each_mutation_of_the_table_turns_the_check_red_and_the_controls_stay_green(self):
        doc, _error = check_prices.load(str(PRICES))
        day = check_prices.parse_date(doc["updated"])
        with tempfile.TemporaryDirectory() as tmp:
            for n, (name, mutate, expect) in enumerate(mutations.PRICE_MUTATIONS):
                folder = os.path.join(tmp, "m%02d" % n)
                os.makedirs(folder)
                problems = mutations.run_price_mutation(doc, day, mutate, folder)
                self.assertEqual("red" if problems else "green", expect, name)
        self.assertGreaterEqual(sum(1 for m in mutations.PRICE_MUTATIONS if m[2] == "red"), 10)


check_coverage = load("check_coverage", ROOT / "tools" / "check_coverage.py")
side_tool = load("side_by_side_tool", ROOT / "tools" / "side_by_side.py")
# The run folder of the plan, when this plugin is checked on the machine that holds it (plan section 2, E1).
RUN = os.environ.get("BILLCALL_RUN_DIR", "")


class FactsAndMaps(unittest.TestCase):
    """Stage E1: the facts table and both idea maps leave nothing out, and the owner's side-by-side tables are
    the price table itself."""

    def test_facts_and_both_maps_cover_everything_the_snapshot_names(self):
        coverage, error = check_coverage.load_coverage()
        self.assertIsNone(error)
        self.assertEqual(check_coverage.check_all(str(ROOT), coverage), [])

    def test_the_russian_facts_have_the_same_rows(self):
        def rows(path):
            return [line.split("|")[1:3] + line.split("|")[4:7] for line in path.read_text(encoding="utf-8").splitlines()
                    if re.match(r"^\| F\d+ \|", line)]
        en = rows(ROOT / "data" / "facts-2026-10.md")
        ru = rows(ROOT / "data" / "ru" / "facts-2026-10.md")
        self.assertGreater(len(en), 60)
        self.assertEqual([[c.strip() for c in r] for r in en], [[c.strip() for c in r] for r in ru])

    def test_the_side_by_side_tables_are_written_from_the_price_table(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = side_tool.main(["--check"])
        self.assertEqual(code, 0, out.getvalue())

    def test_a_table_left_behind_a_price_change_is_found(self):
        made = side_tool.tables(billcall, TABLE)
        text = (ROOT / "data" / "facts-2026-10.md").read_text(encoding="utf-8")
        stale = text.replace("$20 a seat a month", "$19 a seat a month", 1)
        self.assertNotEqual(stale, text)
        self.assertEqual(side_tool.fill(stale, made)[0], side_tool.fill(text, made)[0])

    @unittest.skipUnless(RUN and os.path.isdir(RUN), "BILLCALL_RUN_DIR names no run folder on this machine")
    def test_against_the_live_plan_and_run_folder(self):
        coverage, _error = check_coverage.load_coverage()
        plan = check_coverage.read_text(os.path.join(RUN, "plan", "plan.md"))
        self.assertEqual(check_coverage.check_plan(plan, coverage) + check_coverage.check_run_dir(RUN, coverage), [])

    def test_each_mutation_of_the_facts_and_maps_behaves(self):
        coverage, _error = check_coverage.load_coverage()
        with tempfile.TemporaryDirectory() as tmp:
            for n, (name, section, mutate, expect) in enumerate(mutations.COVERAGE_MUTATIONS):
                folder = os.path.join(tmp, "c%02d" % n)
                os.makedirs(folder)
                problems = mutations.run_coverage_mutation(coverage, section, mutate, folder)
                self.assertEqual("red" if problems else "green", expect, name)


class NoNetwork(unittest.TestCase):
    MODULES = ("urllib", "http.client", "socket", "requests", "httpx", "ftplib", "smtplib")

    def offenders(self, text):
        found = []
        for line in text.splitlines():
            words = line.strip().split()
            if len(words) >= 2 and words[0] in ("import", "from"):
                name = words[1].split(",")[0]
                if any(name == m or name.startswith(m + ".") for m in self.MODULES):
                    found.append(line.strip())
        return found

    def test_no_network_module_in_any_script(self):
        for path in list((ROOT / "skills").rglob("*.py")) + list((ROOT / "tools").rglob("*.py")):
            self.assertEqual(self.offenders(path.read_text(encoding="utf-8")), [], str(path))

    def test_control_the_scan_finds_a_planted_import(self):
        self.assertEqual(self.offenders("import os\nfrom urllib.request import urlopen\n"),
                         ["from urllib.request import urlopen"])


if __name__ == "__main__":
    unittest.main()
