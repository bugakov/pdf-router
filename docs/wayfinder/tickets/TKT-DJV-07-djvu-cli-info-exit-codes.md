# TKT-DJV-07 — CLI --info and exit codes for djvu

## Question

Follow the docx front-door precedent (TKT-DX-06): `--info` routes through
the one gate for djvu too.

- what `--info` prints for a djvu (page count, size, text-layer presence,
  scan parameters — whatever the inspector metadata exposes);
- exit codes: djvu-unsupported-today vs routable — both must be
  usage-error 1 territory before an inspector exists, then normal flows
  after;
- message wording: the current forward-looking error ("DJVU detected, but
  the router does not support it yet") — does it survive, and what does it
  become under each strategy?

Blocked by: TKT-DJV-02.

## Resolution

_(open)_
