# Codex Engineering Harness 3.0.0

This local implementation follows the supplied V3 workflow contract. Shared
dependencies are installed once; skills, MCP and optional planning hooks are
enabled per Git project. The original formal manual is in `docs/`.

The bound runtime on this machine is `/usr/lib/chatgpt/resources/codex` 0.159.2.
The separate Snap CLI on PATH is older and is not the Harness runtime.

```bash
harness --help
harness enable /absolute/path/to/git-repo
harness doctor /absolute/path/to/git-repo --deep
harness start /absolute/path/to/git-repo
```

The default L1 profile uses project skills and four project MCP servers. L2
planning hooks require explicit profile selection and native user trust:

```bash
harness enable /absolute/path/to/git-repo --profile L2
# Start a new Codex session in this project and review new definitions in /hooks.
```

Do not enable the same skills through both project overlay and the catalog
plugin. `plugins/harness` is a lean alternative skill bundle; the local catalog
provides the plugin entry without registering global MCP or hooks. A local
plugin installation does not establish ChatGPT web availability.

Natural project development requests select the Main Router. `$harness-dev`
is its explicit entry. `implement` and `implement-spec` retain actual explicit
invocation policies. Upstream methods retain their documented side effects;
invocation does not authorize commits or external writes.

Services are loopback-only. PowerContext provides Basic explicit Handoff;
generation and embeddings are not configured. The screenshot provider has a
working local preview and requires a model credential for actual generation.

```bash
python3 scripts/services.py status
python3 scripts/services.py stop
python3 scripts/services.py start
```

Source, package versions, file hashes and third-party notices are locked in
`harness.lock` and `manifests/`. Runtime environments, credentials, databases
and project state are excluded from release archives. Reconstruct a moved
online installation with:

```bash
python3 scripts/bootstrap.py
# Add --services to rebuild and start the optional sidecars.
bin/harness install
```

Maintenance accepts an explicit local release candidate. Read `harness update
--help` for candidate validation and promotion; static checks alone do not
authorize activation. Transaction journals preserve the exact touched files,
and rollback refuses to overwrite later user modifications. Disable projects
before uninstalling the shared launcher. Sidecar stop preserves stored data.

The implementation verifies contract edges and records actual task evidence;
the JSON contract is design data, not a Codex runtime API. `freeze` creates an
immutable source snapshot covering task commits and the applicable working
tree; `review-status` detects source drift and records coverage. Caller reports
do not independently authenticate a reviewer or user authorization. The
Main Router must use real tools and independent contexts for required review.

Read `reports/install-report.md` and `reports/acceptance-summary.json` for the
actual verification scope. UNVERIFIED, PENDING_TRUST and optional provider
blocks are reported separately from executed checks.
