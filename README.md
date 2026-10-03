# Codex Engineering Harness 3.1.0

This local implementation follows the supplied V3 workflow contract. Shared
dependencies are installed once; skills, MCP and optional planning hooks are
enabled per Git project. The original formal manual is in `docs/`.

The bound runtime on this machine is `/usr/lib/chatgpt/resources/codex` 0.160.0.
The separate Snap CLI on PATH is older and is not the Harness runtime.

```bash
harness --help
cd /absolute/path/to/project
harness init
harness start
# Explicit paths also work:
harness enable /absolute/path/to/git-repo
harness doctor /absolute/path/to/git-repo --deep
harness start /absolute/path/to/git-repo
```

`init`, `enable`, and `start` default to the current directory and select the
existing Git root when invoked from a subdirectory. `init` creates a local Git
repository when needed, then enables Harness. First `start` performs the same
initialization automatically. It preserves project files and
creates no commit or GitHub remote. Newly initialized repositories support task
tracking and review before their first commit. Run it inside the actual project;
initialization refuses the home directory, filesystem root, and folders containing
another Git repository. If enablement fails after Git initialization, `.git` is
preserved and the error explains how to retry.

When the locked runtime is the Codex Desktop bundled binary, `harness start`
validates/initializes the project and returns `DESKTOP_PROJECT_READY` with the
exact repository path. It does not exec the bundled binary as a TUI, because that
runtime is app-server capable but is not a complete standalone CLI package. Open
that reported repository as the **primary folder** in Codex Desktop and start a
new chat. If `HARNESS_CODEX_BINARY` points to a verified complete standalone
Codex CLI, bootstrap records `codex_launch_mode=cli` and `harness start` execs it.

The default L2 installation profile uses project skills, four project MCP servers,
PWF hooks, and context checkpoint/recovery handlers in one project-owned hook file.
Task risk is classified independently. Existing bindings keep their selected profile.
Every new or changed hook still requires native user trust:

```bash
harness enable /absolute/path/to/git-repo --profile L2
# Or initialize and enable the current project:
harness init --profile L2
# Start a new Codex session in this project and review new definitions in /hooks.
```

Use `harness init --profile L1` for an installation without hooks. After a local
Harness change, `harness refresh /path/to/project` reconciles owned overlays,
preserves unmanaged instructions and task data, and retains the project's profile.
`harness defaults` shows the current policy.

Do not enable the same skills through both project overlay and the catalog
plugin. `plugins/harness` is a lean alternative skill bundle; the local catalog
provides the plugin entry without registering global MCP or hooks. A local
plugin installation does not establish ChatGPT web availability.

Natural project development requests select the Main Router. `$harness-dev`
is its explicit entry. `implement` and `implement-spec` retain actual explicit
invocation policies. Upstream methods retain their documented side effects;
invocation does not authorize commits or external writes.

Services are loopback-only. Initialization binds a stable project PowerContext
Scope when its existing service is ready; otherwise local checkpoints remain usable.
The current Codex session supplies task summaries for Basic Handoff and image
understanding for screenshot-to-code work. Neither route copies login credentials.
Third-party screenshot generation and PowerContext Full background extraction/
embeddings still need separate supported provider configuration; ChatGPT login is
not a reusable API key. The screenshot sidecar's local preview remains available.

Long tasks follow checkpoint → native `/compact` → recovery verification. At real
reported context usage of 70/80/90%, warn/require checkpoint/stop new implementation.
If the host provides no usage reading, report UNKNOWN and checkpoint at milestones;
there is no autonomous percentage monitor. Two compactions recommend a formal
Handoff. Ordinary compaction never creates one. See the
[context recovery guide](skills/harness-dev/references/context-recovery.md).

```bash
harness context status . --used-percent 80
harness context checkpoint . --task TASK --facts .harness/task/TASK/context-facts.json --resume-node T00
harness context verify . --task TASK
harness context handoff . --task TASK
harness context recall . --task TASK
```

Checkpoints retain Git/Spec/PWF/test references and task facts independently of chat.
Missing summaries or changed versions return a recovery conflict. Tests/review facts
are agent-recorded, so Main must verify their evidence and current authorization.
PowerContext returns exact Scope/artifact/revision references as untrusted history;
it never overwrites current code. New handlers are ineffective until trusted; direct
script checks do not prove native lifecycle delivery.

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
