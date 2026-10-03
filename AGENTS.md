# Repository Guidelines

## Project Structure & Module Organization

- `bin/harness` exposes the Python CLI. `harness_core/` implements project overlays, transactions, review snapshots, registration inventory, workflow state, and candidate acceptance.
- `tests/test_harness_core.py` contains regression tests with temporary Git repositories and isolated installation state.
- `scripts/` provides dependency bootstrap, service management, and runtime probes. `templates/` contains project configuration and Docker service recipes.
- `skills/` contains workflow instructions; `plugins/harness/` is their lean distributable mirror. Keep both copies synchronized.
- `contracts/workflow.json`, `harness.lock`, and `manifests/` define routing, pinned dependencies, ownership, and licenses. `docs/` contains the formal HTML manuals and diagrams.
- `vendor/`, `runtimes/`, `private/`, and `.test-projects/` are managed local artifacts. `reports/` holds verification evidence; `dist/` holds generated archives.

## Build, Test, and Development Commands

Run commands from this repository's root:

```bash
python3 bin/harness --help
python3 bin/harness check
python3 -m unittest discover -s tests -v
python3 scripts/services.py status
python3 bin/harness package --output dist/harness-dev.tar.gz
```

These commands inspect the CLI, validate structure, run tests, inspect sidecars, and package source. The Python control plane needs no compilation. In a fresh checkout, `python3 scripts/bootstrap.py` restores pinned dependencies; `--services` also builds and starts sidecars. Avoid rebuilding dependencies underneath active sessions.

## Coding Style & Naming Conventions

Use four-space Python indentation, `snake_case` functions and modules, and `PascalCase` classes. Follow surrounding code and avoid unrelated formatting changes. No formatter or linter is currently configured. Preserve supported configuration schemas and contract node IDs. Update source hashes and skill mirrors when their inputs change; do not edit installed dependency artifacts directly.

## Testing Guidelines

Use standard-library `unittest`, `test_*.py` files, and `test_*` methods. No numeric coverage threshold is configured. Add meaningful regressions for changed behavior, especially ownership conflicts, rollback, privacy exclusions, stale evidence, and project isolation. Use temporary repositories and override `HARNESS_STATE_DIR`, `HARNESS_BIN_DIR`, and `CODEX_HOME`. Distinguish fixture checks from native MCP, model, and Hook execution; report unexecuted checks as `UNVERIFIED`.

## Commit & Pull Request Guidelines

The two existing commits use imperative subjects beginning with “Build” and “Resolve”; no Conventional Commits scheme is established. Keep changes focused. PR descriptions should explain the problem, resulting behavior, validation scope, and any lock, permission, or migration changes. Link relevant issues and include screenshots for visual changes.

## Security & Configuration

Preserve unknown user configuration and journal managed mutations. Keep credentials, machine bindings, databases, caches, and task state out of release archives. Never forge Hook trust. Keep services loopback-only and require explicit authorization for remote publication.
