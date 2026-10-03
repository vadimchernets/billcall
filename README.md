# billcall

[![DOI](https://zenodo.org/badge/DOI/10.5281/zenodo.23116722.svg)](https://doi.org/10.5281/zenodo.23116722)

What AI costs a company — counted from one dated price table, before anything is bought and every week
after. A [Claude Code](https://claude.com/claude-code) plugin of Poly A1, for the person in a company who
sets AI up and the one who pays for it. Repository: [github.com/vadimchernets/billcall](https://github.com/vadimchernets/billcall).

**billcall counts and compares; it buys nothing.** The person responsible for the company's accounts decides and buys — on numbers that name their source and their day.

## What it does

| Command | What the company gets |
|---|---|
| `estimate` | The monthly bill of a mix: team seats for people, API work for scripts, seats already paid for (Microsoft 365 Copilot, Google Workspace), the price of one accepted task, alternatives side by side (a model swapped for a cheaper one shows its quality index on both sides, so a weaker line never reads as a plain saving), people who work in Cowork or Claude Code all day on the lowest seat tier (the shared usage pool runs out there first) — and every vendor rule the mix breaks: a personal plan for a company, scripts on a coding plan, too few or too many seats, a vendor the company policy leaves out. Examples: `data/examples/agency-10.json`, `data/examples/firm-100.json`. |
| `use` | The check before a script or a helper program runs on a plan: may this row carry scripts, or this many people? Exit 1 and the vendor's rule when the terms say no (routecall's crew asks it). |
| `week` | What was spent: Claude Code's own logs on this computer at list price (each API response counted once), the Team or Enterprise spend-report CSV per person and model (usage-credit spend), an OpenTelemetry export of `claude_code.cost.usage`, and the seats nobody used — from activity (`--activity`, the analytics export, or `--otel`), so a person working inside the seat allowance is never listed as unused. The report goes into the folder in the person's language. |
| `trace` | What the agents did and what it cost, per person, project and day: sessions, subagent runs, API responses, tool calls, tokens and the API list price, read from the company's Claude Code session logs on its own computers. See [Trace](#trace). |
| `guard` | A budget alarm: today's and this month's spend against `max_usd_per_day` / `max_usd_per_month` from the company policy file, one line at 50, 80 and 100% at session start, in the person's language (en, es, pt, ru, uk). With `claude_billing` `seat` (Team, Pro, Max) the hook stays silent and `guard` prints the in-seat usage as one line that is not money. |
| `contract` | Claude Team (all Premium) against Enterprise (a seat plus usage at API rates) and the API use per person where they meet; contract rates as the `modelPricing` block that only managed settings accept. |
| `local` | An own GPU machine against renting the same hours: its month (price over 36 months plus electricity) and the share of hours from which it costs less. |
| `side-by-side` | `us`: the US five (Anthropic, OpenAI, Google, xAI, Meta) with Microsoft and GitHub — team seats, the Enterprise contract, plans for one person, the strongest and the cheapest API, coding agent plans. `china`: DeepSeek, Kimi, Qwen, GLM, MiniMax, MiMo, BytePlus — API prices, plans for people, where data is processed, the weights' licence. `local`: own machines and rent with the lead and on-call models to run on them. One Markdown table each; every figure with the day it was read and its page. |
| `prices` | The rows of the price table, filtered by kind, use or vendor. |

## Installing

From the Poly A1 catalogue, by its link — no git and no account needed:

```
/plugin marketplace add https://raw.githubusercontent.com/vadimchernets/poly-a1-plugins/main/.claude-plugin/marketplace.json
/plugin install billcall@poly-a1
```

Then say what you want counted: "what would Claude cost our 12 people", "what did we spend this week",
"set a budget of 40 dollars a day". The skill writes the company profile with you and runs the command.

## Trace

`billcall trace` reads Claude Code's session logs — `~/.claude/projects/<project>/<session>.jsonl` and the subagent
logs under `<session>/subagents/`, `$CLAUDE_CONFIG_DIR` first — and answers what the agents did and what it cost:

```
python3 skills/billcall/scripts/billcall.py trace --days 7
python3 skills/billcall/scripts/billcall.py trace --logs ann=/backup/ann/projects --logs bob=/backup/bob/projects \
    --since 2026-10-01 --until 2026-10-08 --report company/
python3 skills/billcall/scripts/billcall.py trace --ndjson > responses.ndjson
```

- **Per person, project and day:** sessions, subagent runs, API responses, tool calls (Bash, Read, Edit, web search
  and the rest, counted by name), tokens and the cost. A person is the folder's name (`ann=...`) or `user@machine`;
  a project is the session's working folder; a day is the local date.
- **Cost:** input, output, cache reads, 5-minute and one-hour cache writes, each at its rate in `data/prices.json`
  (US-only inference at 1.1x). A response written on several lines counts once (message id and request id), a tool
  call once (its block id). A model the table does not price shows its tokens and "no price".
- **What the figure is:** on the API and on Enterprise (since 2026-09-01 a seat is access only and all usage is billed
  at API rates) it is the bill; inside a Team seat's allowance it is the API-equivalent value of the work.
- **Output:** Markdown tables, `--json` totals, or `--ndjson` with one line per response for a spreadsheet or BI tool.
  Everything runs on the company's computer from its own files.

## The price table

`data/prices.json` is the one source of every price: seats, API tokens, coding and token plans, free tiers,
hardware, rent and training. Each row carries `url`, `checked` (the day it was read), `status` (`official`,
`official-api`, `secondary`, `unverified`, `stale`), `use` (`personal`, `team-interactive`, `automated`) and,
where it matters, `valid_until` for a promotion. `python3 tools/check_prices.py data/prices.json --strict`
must report 0 problems; it reddens on a missing url, a status outside the list, a row without `use`, a price
read too long ago and a promotion past its date.

## The facts and the idea maps

`data/facts-2026-10.md` (Russian: `data/ru/facts-2026-10.md`) checks every claim of the track's plan that carried a
source tag or was not confirmed, against the vendor's own page: confirmed, refuted (with the right value) or
confirmed on a third-party page, each with its link and day. Its last part holds the three side-by-side
tables, written from `data/prices.json` by `tools/side_by_side.py`. `data/late-opinions-map.md` and
`data/jev-ideas-map.md` give every idea of the AI council's late opinions and of the owner's JEV notes a verdict
and the place it went. `tools/check_coverage.py` reddens when a plan line, an opinion file or a JEV range has no
row; `tools/side_by_side.py --check` reddens when the tables lag behind the price table.

## The company policy file

`company-ai-policy.json` is shared by the company plugins (firmcall writes it; gatecall, routecall and
billcall read it). billcall reads `max_usd_per_day`, `max_usd_per_month`, `allowed_providers`,
`deny_providers`, `language` and `claude_billing` (`api`, the default, or `seat`). It is looked for in this order: the managed copy an administrator installed
(`/Library/Application Support/ClaudeCode/`, `/etc/claude-code/`, `C:\Program Files\ClaudeCode\`), then
`$COMPANY_AI_POLICY`, then the project folder and its `.claude/`, then `~/.claude/`.

## What it needs

Python 3.8+ and its standard library — no dependencies, no network module. Skills run their script through
`hooks/python.sh` (PowerShell: `hooks/python.ps1`), which finds a real Python and never starts the Apple or
Microsoft Store stub.

## Checks

`python3 -m pytest -q tests` — the examples add up to the plan's figures, every vendor rule and every policy rule
reddens when it is broken (and a tiered plan's seats count together), logs are counted once per response, the
budget alarm speaks at its thresholds and stays silent without a budget and on a seat, `trace` counts the fixture
logs in `tests/fixtures/trace/` per person, project and day, the `modelPricing` block keeps the
documented shape, the price-table mutations redden, and no network code is present. `python3 tools/mutate_code.py`
breaks billcall's own rules one by one in a copy and expects the tests to go red; its last line counts the
mutations that misbehaved.

## Licence

Apache-2.0. See `LICENSE` and `NOTICE`.
