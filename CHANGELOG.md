# Changelog

## 0.1.4 — 2026-10-03

- `trace`: what the agents did and what it cost, per person, project and day, from the company's Claude Code session
  logs (`~/.claude/projects/*/*.jsonl` and the subagent logs under them, `$CLAUDE_CONFIG_DIR` first): sessions,
  subagent runs, API responses, tool calls by name, web searches, tokens and the API list price of the price table
  (5-minute and one-hour cache writes priced apart, a model without a row named with its tokens). A team's folders
  are named per person (`--logs ann=<folder>`); `--since`/`--until`, `--json`, `--ndjson` (one line per response),
  `--report` in the person's language. Fixture logs in `tests/fixtures/trace/`.
- `guard` on a seat: `claude_billing` in the company policy (`api`, the default, or `seat`) or `--billing`. Usage inside
  a Team, Pro or Max seat allowance is not metered in dollars (code.claude.com/docs/en/costs), so on `seat` the hook
  stays silent and `guard` prints one in-seat line that is not money; the alarm is for API, Enterprise and usage
  credits.
- `week`: seats nobody used come from activity — `--activity` (the analytics export) or `--otel` — not from the spend
  report, which covers usage-credit spend only: a person working inside the seat allowance is active at $0.
- `anthropic-enterprise`: since 2026-09-01 the seat is access only and all usage is billed at API rates; self-serve
  from 20 seats, sales-assisted from 50; Standard/Premium seat plans move to the single seat at renewal
  (support.claude.com/en/articles/9797531, read 2026-10-03).
- `cursor-teams`: Standard $40 and Premium $120 a user a month (cursor.com/docs/account/teams/pricing, read
  2026-10-03); the side-by-side tables in `data/facts-2026-10.md` and `data/ru/` follow.
- New words in all five languages: `trace_title`, `trace_file`, `guard_in_seat`.

## 0.1.3 — 2026-10-03

- Primary sources for all eight `secondary` rows of `data/prices.json` (now `official`, checked 2026-10-03; no row is
  `secondary` any more): `openai-codex-seat-usage-based` (openai.com/index/codex-flexible-pricing-for-teams/ and help
  articles 20001492, 20001106), `zai-glm-coding-plan` (the plan objects in the script z.ai/subscribe loads),
  `apple-mac-studio-m5-ultra-256gb` (Apple Store configuration pages), `nvidia-dgx-spark` (NVIDIA Marketplace, read in
  a desktop browser: 128 GB $6,950.00; the 64 GB $4,999 from the NVIDIA blog), `coursera-for-teams`
  (coursera.org/business/compare-plans page data), `codecademy-teams` (codecademy.com/business/pricing),
  `linkedin-learning` (LinkedIn Help a768297), `litellm-enterprise` (docs.litellm.ai/docs/enterprise: "Pricing is
  based on usage. Contact us for a quote" - the row now has no price, like the other contact-sales rows; the
  third-party ~$30k a year stays only in `price_note`).
- Figures corrected against those pages: Codecademy Teams $25 -> $24.92 a seat a month (billed annually, from 2 users);
  LinkedIn Learning "about EUR 350" -> USD 379.88 per licence a year (Learning for Teams self-serve, 2 to 20 online,
  at most 50); GLM Coding Plan 18 / 80 / 168 dates from 2026-07-30 (official notice), not 2026-08-24; the Codex-only
  seat note drops "about $100-200 per developer per month" (on no official page) and says the 2026-06-24 cut-off is
  for Business only.
- Facts F29, F82, F88 now `confirmed`, F60 and F79 `refuted`, each on the vendor's own page (en and ru).
- `tools/mutations.py`: the "secondary row without a note" mutation marks a row secondary itself, since the table
  may have none.
- Quality index added for `alibaba-api-qwen3-8-flash` (40), `openai-api-gpt-6-luna` (38, max) and
  `google-api-gemini-2-5-flash-lite` (9), from artificialanalysis.ai, read 2026-10-03.
- Cheap class ratio: `billcall side-by-side china` now prints how many times the cheap Chinese models (DeepSeek V4.1
  Flash, Qwen3.8-flash, GLM-5.3-Flash, MiMo-v2.6-flash, MiniMax M3; Kimi K2.7-code shown apart) are cheaper than
  Claude Haiku 4.5 and Sonnet 5.5, and against GPT-6 Luna, Gemini 3.8 Flash and Gemini 2.5 Flash-Lite, per 1M tokens
  (3 input : 1 output, list price), with the quality index on both sides and a headline in the person's language
  (`--lang`); `tools/side_by_side.py` writes it into a new section of `data/facts-2026-10.md` and `data/ru/`.
- Wording: no disclaimers. README and the skill say what billcall does, once; a product outside the price table
  gets its vendor's page. Tests: a tone check reddens on disclaimers, excuses and apologies in what people read
  (five languages). The budget stop, the vendor rules and the quality index are unchanged.

## 0.1.2 — 2026-10-03

- README: the Zenodo DOI badge (the concept DOI always points to the latest version).

## 0.1.1 — 2026-10-03

- Zenodo DOI: the repository is archived on Zenodo; this release is the first one it records (same content as 0.1.0).

## 0.1.0 — 2026-10-02

- First release: `estimate`, `use`, `week`, `guard`, `contract`, `local` and `prices`, one skill and one
  session-start hook (the budget line, silent without a budget).
- `data/prices.json` with the constant `claude-code-api-cost-per-active-day` ($13, code.claude.com/docs/en/costs,
  as read for the plan on 2026-10-02), used to set a seat against the API for the same person.
- `side-by-side us|china|local`: the owner's three tables from the price table, every figure with its day and page.
- `estimate` weighs a swapped model by its quality index (a cheaper, weaker line is named as weaker) and tells
  people who work in Cowork or Claude Code all day on a Standard seat that the shared usage pool runs out first
  (constant `cowork-usage-factor`); an alternative may replace automated work too.
- The budget line at session start speaks the person's language (en, es, pt, ru, uk).
- `week` never prices a newer model version at an older row (`claude-opus-5-6` is not `claude-opus-5`).
- `data/facts-2026-10.md` (+ `data/ru/`), `data/late-opinions-map.md`, `data/jev-ideas-map.md`, checked by
  `tools/check_coverage.py`; the side-by-side tables by `tools/side_by_side.py --check`.
- Example profiles `data/examples/agency-10.json` and `data/examples/firm-100.json`.
- The company policy file `company-ai-policy.json`: budgets, `allowed_providers`, `deny_providers`, language.
- `modelPricing` checked against the shape on code.claude.com/docs/en/settings-reference (multiplier above 0
  and at most 10; overrides with input, output, cacheRead and cacheWrite from 0 to 10000), managed settings only.
