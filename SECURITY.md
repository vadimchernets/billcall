# Security

## What billcall touches

- **Claude Code's session logs** on this computer (`$CLAUDE_CONFIG_DIR/projects`, `~/.config/claude/projects`,
  `~/.claude/projects`), read-only. From each line it keeps the model name, the token counts and the time - never
  the text of a conversation.
- **Files the person names**: a company profile, a Team or Enterprise spend-report CSV, a roster of e-mail
  addresses, an OpenTelemetry export, contract rates. Read-only.
- **The company policy file** `company-ai-policy.json` (budgets, allowed and denied vendors, language), read-only.
- **Writes** only what it is asked to: the weekly report into the folder named with `--report`, the
  `modelPricing` block into the file named with `--out`.

## What it never does

- Never buys, subscribes, opens a checkout or asks for a card number, a password or an API key.
- Never goes to the network: Python's standard library only, and no network module is imported (a test fails the
  build if one appears).
- Never writes managed settings itself: the `modelPricing` block goes to whoever manages the company's settings.

## Reporting

Open an issue on github.com/vadimchernets/billcall, or write to the author through the Poly A1 support address.
