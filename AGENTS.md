# AGENTS.md

## Agent skills

### Issue tracker

GitHub Issues for `bugakov/pdf-router`. No external PRs as a triage surface. See `docs/agents/issue-tracker.md`.

### Triage labels

Default vocabulary: `needs-triage`, `needs-info`, `ready-for-agent`, `ready-for-human`, `wontfix`. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context — one `GLOSSARY.md` and `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Compilation and CI policy

**Strictly prohibited:** Local compilation (`cargo build`, `cargo test`, `cargo check`, `cargo clippy`, `cargo run`, `maturin`), or any command creating heavy `target/` build directories on this host. The host has strictly limited disk space, and compilation artifacts cause 100% disk exhaustion, crashing the system and live applications.

**Mandatory:** All compilation, linting, test suites, and artifact verification must run exclusively in GitHub Actions (`.github/workflows/ci.yml`). Commit and push to `feat/**` branches, then monitor the CI run with `gh run list` / `gh run watch` / `gh run view`. See `docs/agents/ci-and-compilation.md`.
