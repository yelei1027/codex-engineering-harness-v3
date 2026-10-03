# PowerContext Basic explicit Handoff

Isolated Docker Server built from commit `6b2f6e8a4c1aac59fca75662aca26d78748bfa29`.
The upstream shallow clone has no release tags; the local package version is explicitly
`0.0.0+g6b2f6e8`, and the commit identifies the installed source.
Dependencies use upstream `uv.lock` with `--frozen --no-dev --extra cli --extra server`.
Base container images are pinned by digest. The runtime Dockerfile preserves upstream behavior
and removes only a BuildKit cache mount unsupported by this host's Docker build path.

From the Harness root:

```bash
python3 scripts/services-build.py --service powercontext
python3 scripts/services.py start --service powercontext
python3 scripts/services.py status --service powercontext
python3 scripts/services.py doctor --service powercontext
python3 scripts/services.py stop --service powercontext
```

The machine binding records `http://127.0.0.1:18080`, MCP `/mcp/`.
The host publish port can be selected through `POWERCONTEXT_PORT` when starting Compose;
update machine binding and client URLs together. The Server binds inside its Docker network only;
the host publish address is always loopback. The `.env` file is mode 600.
The named volume `codex-harness-powercontext-data` contains persistent SQLite data and is
preserved by stop. It must not be included in distribution archives.

The installation smoke created a separate Scope and committed only generated fixture content.
`scripts/services-smoke.py` prepares and commits a fresh fixture Handoff, then starts a separate
Python receiver process to resolve its exact Revision. Reports are in `reports/powercontext-smoke.json`.
The live MCP discovery report includes all 47 actual advertised tool schemas.

No generation or embedding provider is configured. Basic explicit writes, Handoff, and FTS
are usable; Full automatic memory is unverified. The Harness registers one direct HTTP MCP source.
The upstream PowerContext host plugin and automatic prompt/Stop hooks are not installed by this
Basic profile. Its plugin-specific `doctor codex` consequently reports plugin absent; service
doctor and real MCP/Handoff checks are independently recorded.
