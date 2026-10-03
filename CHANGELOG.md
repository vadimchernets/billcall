# Changelog

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
