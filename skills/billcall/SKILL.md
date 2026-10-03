---
name: billcall
description: Count and compare what AI costs a company - before buying and every week after. Use it when someone from a company asks what Claude, ChatGPT, Gemini, Copilot or the Chinese models would cost for their team, which seats to buy for whom, whether scripts should run on the API, what the company spent this week, whether Team or Enterprise is cheaper, whether an own GPU machine pays off, or wants a budget alarm. It works from one dated price table and buys nothing.
argument-hint: "[estimate | use | week | guard | contract | local | side-by-side | prices] [what to count]"
allowed-tools: Bash(sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py *) PowerShell(${CLAUDE_PLUGIN_ROOT}/hooks/python.ps1 billcall say skills/billcall/scripts/billcall.py *) Read Write
---

# billcall: what the company's AI costs

## Running billcall's scripts (Mac, Linux, Windows)

Every script command on this page is written for the **Bash** tool and starts with
`sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/…`. If your shell tool is
**PowerShell** (Windows without Git Bash), only the start changes: write the launcher's path bare, with no quotes
and no `&` — `${CLAUDE_PLUGIN_ROOT}/hooks/python.ps1 billcall say skills/billcall/scripts/…` — and keep the rest,
on one line; that is the form this skill's permission covers. Only if that path has a space in it, write
`& "${CLAUDE_PLUGIN_ROOT}/hooks/python.ps1" …` instead (the person is then asked once). Never call `python3`,
`python` or `py` yourself: the launcher finds a real Python 3.8+ (`python`, then `py -3`, then `python3`) and never
starts the Microsoft Store or Apple stub. If it answers with one line saying billcall "is paused" until this
computer has Python 3, tell the person that in one plain line and do the arithmetic by hand from
`data/prices.json` — never show them a Python error and stop.

The user said: $ARGUMENTS

Answer in the person's language. Money is the subject here: say every figure plainly, with the day the price was
read and where. **billcall counts and compares; it buys nothing.** The one who decides and buys is the person responsible for the company's accounts — your job is that
they decide on the right numbers.

## Where every number comes from

`data/prices.json`: one row per product, each with the page it was read from (`url`), the day (`checked`) and how it
was read (`status`: `official` - the vendor's own page; `secondary` - another source, named in `note`). Say
"from a secondary source" when a row says so. For a product outside the table, name the vendor's page where its
price is read — never guess a price.

## 1. What the AI will cost (estimate)

Ask three things, in plain words, one at a time:

1. Who will use it and how much — people who work in an agent most of the day, people who write letters and
   documents, people who rarely touch it.
2. What should run on its own — nightly reports, sorting requests, anything a script or a schedule does.
3. What is already paid for — Microsoft 365 with Copilot, Google Workspace (Gemini is inside), GitHub Copilot.

Write their answers as `billcall-profile.json` in the company's folder. The shape (two complete examples are in
`${CLAUDE_PLUGIN_ROOT}/data/examples/agency-10.json` and `firm-100.json` — read one first):

```json
{
  "schema": 1, "name": "…", "billing": "annual", "active_days_per_month": 21,
  "roles": [{"id": "builders", "people": 3, "product": "anthropic-team-premium", "surface": "cowork"},
            {"id": "office", "people": 80, "product": "microsoft-365-copilot-business", "already_paid": true}],
  "automated": [{"id": "nightly", "product": "anthropic-api-claude-haiku-4-5",
                 "input_mtok": 20, "output_mtok": 2, "modifiers": ["Batch API"]}],
  "tasks": {"per_month": 600, "accepted_share": 0.8},
  "alternatives": [{"name": "…", "replace": {"office": {"product": "anthropic-team-standard", "already_paid": false}}}]
}
```

`surface` (`cowork` or `code`) marks people who work in an agent most of the day: the estimate then says that the
shared usage pool runs out first on a Standard seat. An alternative may replace an `automated` item's product too;
the estimate then prints the quality index of both models, so say plainly when the cheaper one is also weaker.

Product ids are the `id` of rows in the table; list them with
`sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py prices --vendor Anthropic`
(or `--use automated`, `--kind seat`). Then:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py estimate "<folder>/billcall-profile.json"
```

Show the person: each role's line, the new money a month, what is already paid, the whole bill, the price of one
accepted task, and each alternative. Read the notes aloud in one line each — a promotional price ends on its date,
a price read long ago is to be re-read.

**A `BROKEN:` line is a rule of the vendors' own terms or of the company's policy, and the mix is fixed before
anyone buys it:**

- a personal plan (Pro, Max, a personal coding plan) for a company of several people — every person gets a seat on a
  team plan; one account per person, never a shared one;
- scripts, CI or background jobs on a seat or a coding plan — they go on API rows (`use: automated`): every coding
  and token plan read on 02.10.2026 forbids non-interactive use;
- fewer seats than a plan sells (Enterprise from 20, BytePlus Team from 5) or more than it holds (Team up to 150);
- a vendor the company's `company-ai-policy.json` leaves out (`allowed_providers`) or names in `deny_providers`.

Change the profile, run it again, and show the kept version. People who work in an agent all day usually come out
cheaper on a seat than on the API (the estimate prints both: the API averages about $13 per developer per active
day, code.claude.com/docs/en/costs); a seat also brings the phone remote, cloud sessions and the advisor, which an
API key does not.

## 2. Before another program runs on a plan (use)

Before any script or helper program (routecall's crew, a scheduled job) runs on a plan, ask the table:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py use --product zai-glm-coding-plan --for scripts
```

Exit 1 with a `BROKEN:` line means the vendor's terms say no — pick an API row (`--use automated` in `prices`)
instead. `--for people --people 12` asks the same for a group of people.

## 3. What was spent (week)

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py week --days 7
```

That counts Claude Code's own logs on this computer at list price, the way `/usage` does — on a subscription seat
it is what the same work would have cost on the API. For the whole organisation add the spend report: on Team and
Enterprise an admin exports it from the organisation's analytics as a CSV (per person and per model); add
`--team-csv "<file>"`. With `--roster "<file>"` (one e-mail per line: who holds a seat) the report lists the seats
nobody used. A company that exports OpenTelemetry gives `--otel "<file>"`. To leave the report in the folder in the
person's language: `--report "<folder>" --lang ru` (en, es, pt, ru, uk) — the file is named in that language.

## 4. A budget alarm (guard)

Write the budget once: `max_usd_per_day` and/or `max_usd_per_month` in `company-ai-policy.json` (the company's
policy file; firmcall writes it, or write it yourself in the company folder), or `{"day": 30, "month": 500}` in
`~/.billcall/budget.json` for one person. From then on billcall's session-start hook says one line at 50%, 80% and
100%. To check now:

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py guard
```

At 100% tell the person in one line and ask the one responsible before going on. On Team and Enterprise the hard cap
is the organisation's spend limit in the admin settings; billcall's line is the early warning on this computer.

## 5. Team or Enterprise, and contract rates (contract)

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py contract --people 40 --usage 60
```

compares all-Premium Team seats with Enterprise (a seat plus usage at API rates) and prints the API use per person
below which Enterprise costs less. When the company has contract rates, `contract --discount 15` or
`contract --rates "<file>"` prints the `modelPricing` block that makes Claude Code show spend at those rates. It
works only from managed settings (server-managed settings, an MDM policy or managed-settings.json) — hand it to
whoever manages the company's settings (firmcall's job).

## 6. An own machine (local)

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py local --hardware nvidia-rtx-pro-6000-96gb --rent runpod-rtx-pro-6000 --watts 600
```

prints the machine's month (its price over 36 months plus electricity) against renting the same hours, and the
share of the month's hours from which the own machine costs less. Ask the person for the watts on the machine's
label; without them electricity is left out and the line says so.

## 7. Everyone side by side (side-by-side)

When the person asks "what do OpenAI, Google, xAI, Meta offer a company", "what do the Chinese models cost" or
"which model on which machine":

```
sh "${CLAUDE_PLUGIN_ROOT}/hooks/python.sh" billcall say skills/billcall/scripts/billcall.py side-by-side us
```

(`china` or `local` instead of `us`). It prints one Markdown table from the price table: seats for a team, the
Enterprise contract, plans for one person, the strongest and the cheapest API and the coding agent plans of each of
the US five plus Microsoft and GitHub; the Chinese vendors' API prices, plans for people, where the data is
processed and the weights' licence; own machines and rent with the lead model and the on-call models to run on
them. Every figure names its day and its page — show them as they are. The facts behind the plan's claims (what was
confirmed, what was refuted, with the page) are in `${CLAUDE_PLUGIN_ROOT}/data/facts-2026-10.md` (Russian:
`data/ru/facts-2026-10.md`).

## Which data may go where

billcall counts money; which work may go to which model is gatecall's part (`/gatecall:gatecall`): red data stays
on the company's own machine, and its hooks stop keys, card numbers and lists of people's addresses before they
leave in a prompt.

## If the launcher says there is no Python

Say so in one plain line and keep going by hand: open `data/prices.json`, take the rows by their `id`, multiply
seats by people and tokens by price, and show the arithmetic. The rules above hold the same way.
