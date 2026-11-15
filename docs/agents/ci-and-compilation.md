# CI and Compilation Policy

## Strict Prohibition of Local Compilation

**Local compilation is strictly prohibited on this host.**

Do NOT execute commands that trigger Rust or native compilation locally, including but not limited to:
- `cargo build` (debug or release)
- `cargo test`
- `cargo clippy`
- `cargo check`
- `cargo run`
- `maturin build`, `maturin develop`
- Heavy Python test matrices that generate large binaries or unconstrained caches

### Rationale

The development environment runs on a host with constrained disk space (approx. 30 GB total root partition) shared with critical operational system services and live applications.

Rust compilation generates extensive debug symbols, incremental compilation caches, and intermediate build artifacts inside `target/` that can quickly consume 10–20+ GB. This causes **100% disk exhaustion (`ENOSPC`)**, leading to host system crashes, kernel lockups, and failure of active production background processes.

## Mandatory Delegation to GitHub Actions

All compilation, linting, testing, and release artifact verification must be performed exclusively in **GitHub Actions**:

- **CI Workflow definition**: `.github/workflows/ci.yml`
- **CI Triggers**: Automatically runs on every `push` to branches matching `main` and `feat/**`, as well as on pull requests.

### Agent and Contributor Workflow

1. **Static Authoring**: Write source code, documentation, and tests carefully by inspecting types, logic, and diffs.
2. **Permitted Local Checks**: Only lightweight, non-compiling static checks are permitted locally (e.g. `cargo fmt --check` or fast linters that do not build artifacts). Never invoke build pipelines.
3. **Commit**: Stage and commit changes to a feature branch (`feat/<feature-name>`).
4. **Push & Monitor**:
   - Push branch to remote: `git push origin <branch-name>`
   - Monitor CI run via GitHub CLI or web:
     ```sh
     gh run list --workflow=ci.yml
     gh run watch <run-id>
     gh run view <run-id> --log-failed
     ```
5. **Clean Disk Invariant**:
   - If any `target/` directory is ever created by mistake, it must be removed immediately:
     ```sh
     rm -rf target/
     ```
