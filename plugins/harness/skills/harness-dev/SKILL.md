---
name: harness-dev
description: Develop, fix, refactor, or review code in a project enabled for Codex
  Engineering Harness V3. Route concrete repository work through project verification
  and bounded methods. Pure explanation, casual conversation, and work outside an
  enabled project do not activate this development workflow.
---

# Main Router

Resolve the current project and accepted Harness path; use its `bin/harness --help` and `status <repo>` to verify binding. An explicit request to initialize/use Harness in the current project authorizes `harness init` there: it initializes Git if needed and defaults to L2 project hooks, pending native user trust. Do not initialize a home/collection directory or select a different project. `harness start` performs this setup automatically for an unbound project. Pure explanation ends at ZINFO without initialization. Existing bindings retain their profile; `harness refresh` reconciles a changed local release when authorized.

For the `ocr` and `codegraph` CLI methods, read the bound Harness root's `machine-binding.json` and use the absolute executable in `component_commands.<name>[0]`. Desktop sessions may lack these isolated tools on PATH. Resolve the binding before diagnosing an installation failure or installing another copy. `harness start` adds the managed tool directories to its child session's PATH.

The [workflow contract](references/workflow.json) is design data, not a native Codex API. Read the nodes needed for the current branch, their conditions and edges, rather than injecting the whole contract. Use real tool calls or processes. State transitions are recorded with actual `task` commands shown by `--help`; never fabricate a completed node, tool, child, evidence or API parameter. Main is the only outer orchestrator.

## Select and bind

Use A00/D00 to distinguish development, `review_only`, resume, maintenance and explanation. For development/review: A01→D01 verifies repo, runtime, original dirty changes and necessary tools. Create one task with stable ID and intent, record task-base commit, allowed write set, acceptance and preexisting file ownership. A02 classifies L0 light (small low-risk local change), L1 standard feature/bug, or L2 high-risk/complex (auth, permissions, migrations, billing, deletion, concurrency, cross-service contracts, multiple tickets/unknown large repo). Risk overrides diff size; upgrade when facts warrant it; no unevidenced downgrade.

For L2/long work, Main selects `planning-with-files`, pins PLAN_ID and PWF_PLAN_ROOT to this task, and owns shared plans. Workers use assigned ledgers. The default L2 installation profile enables one project-owned PWF hook source plus context checkpoint handlers; it does not make every task L2 risk. Explicit L0/L1 installation profiles remain hook-free. Verify native trust and real execution before claiming hook recovery works. One-shot review/research/CI uses PLANNING_DISABLED=1 when calling PWF scripts. Skip the development chain for review_only: preserve source, prepare E00 and return ZREPORT; fixes require explicit scope.

## Build an acceptance baseline

Follow D02/R00 only for actual inputs: MarkItDown converts documents with critical sections checked. For screenshots, default to the current Codex session's image understanding, implement code within the authorized project, and verify with the project's browser/render tools. No separate service API key is needed for that route; it is not a verified screenshot-to-code sidecar model call. Use the optional sidecar only after confirming its actual provider configuration. Never extract or pass Codex OAuth/login tokens as third-party API keys. Optional provider failure uses F03 and resumes its recorded caller; missing credentials do not become a core failure.

B00/D03–D09 route only necessary methods: `wayfinder` for major unresolved route choices; Main/real explorer for repository facts (CodeGraph when available); Main/real `research` child for external facts; `grill-with-docs` for material business ambiguity (also load `grilling` and `domain-modeling`); `codebase-design` for module contracts/seams; `prototype` or verified UI sidecar for unresolved interaction. Read the installed method and [behavior manifest](references/skill-behavior.json) before invoking it. Preserve upstream `agents/openai.yaml` policies: explicit-only methods can be read as references by Main; do not claim the runtime implicitly loaded them. Matt tracker workflows require actual `docs/agents/issue-tracker.md` and domain conventions; use the project's chosen tracker, never assume GitHub issue/PR write permission. Local templates are [here](references/project-settings.md).

B07 freezes acceptance and exclusions; U01 checks existing authorization for this concrete scope. C00 uses `to-spec`/`to-tickets` only when needed and builds vertical slices with dependencies. C01 fixes each ticket's inputs, resources, write set, checks and budget.

## Execute bounded work

D11 defaults to the V3 route D12–D18. Use real independent test_author, worker, test_diagnoser, explorer or researcher only when their cost and risk justify dispatch. Record child IDs/processes, write sets and results against the host concurrency budget. Never pretend Main speaking in another role is a subagent. Prefer one worker at a time unless independent writes/worktrees justify concurrency.

T00 chooses behavioral testing or T05 nonbehavioral checks. For TDD read `tdd`, confirm public seams using existing task authorization or necessary clarification, and run one meaningful RED→minimal implementation→GREEN slice (T01–T03). Apply only necessary refactoring and rerun relevant checks. Accept real worker patch/check evidence at R05, integrate within the allowed scope at C02 and continue tickets at D15. V00 validates the complete integrated candidate with the repository's required checks.

`implement` and `implement-spec` remain installed with real allow_implicit_invocation=false. Only an explicit user choice enters X00. Read their locked behavior first: implement invokes review and commits; implement-spec creates integration/worktree/merger/reviewer operations and may affect PRs/tickets. Check the full write/remote/commit permissions BEFORE invocation. Pause duplicate outer implementation/review/commit scheduling in X00, count actual children, keep evidence/permission gates active, then return the same candidate to V00.

## Freeze, review and deliver

E00 creates a new immutable source snapshot under `.harness-artifacts/review/<task-id>/<snapshot-id>/` using actual `freeze <repo> --task ID --base VERIFIED_REF` options. Include task committed range, staged, unstaged and relevant untracked source; preserve real bytes plus manifest, scope, file coverage, checks and review reports. Exclude secrets, DBs, sessions and unrelated business data with explicit reasons. Never silently omit task source. Record coverage per required file and reviewer.

D20: Matt `code-review` is valid for a clean committed range with a resolved fixed point/merge-base, nonempty diff and spec source. Its actual Standards and Spec children count against the same budget. For dirty/mixed targets use the unified frozen snapshot plus OCR delegation preview/rules; workspace preview alone misses task commits. OCR does deterministic file/rule selection; Main or actual child performs review. Read source bytes from the snapshot, account for every file, run independent risk review when D21 requires it, and record real findings/dispositions at E03. Any source change invalidates affected checks/coverage and requires V00/E00 again. Failed, interrupted, missing-file or untested review blocks a pass.

D23 sends review_only to ZREPORT. Development follows P00/D24 with an accurate local delivery. U02/U03/U04 require existing specific authorization before commit/PR/merge/deploy; a skill call grants none. Ensure delivered PR/artifact exactly matches verified reviewed source, check current CI where relevant, and report actual completion. `pr` formats supported evidence; `retro` proposes separately scoped environment work.

## Failure and recovery

F00 selects the real cause and caller: code/test fix→F01 or real test_diagnoser; runtime/ownership/permission→F02; optional capability→F03; source drift→V00/E00; CI→P02. Application publish failure uses F04 and the tested application recovery plan, never a Harness upgrade or assumed database downgrade. Do not reset/stash/add unrelated user edits.

Apply [context checkpoints and provider routing](references/context-recovery.md) for long tasks, compaction and session changes. Main records current semantic facts at milestones and before compaction; hooks can only supplement disk state. Do not infer context percentage when the host does not expose it. Same task: checkpoint → compact → verify. Formal session change: wait/stop actual workers, checkpoint, synchronize the bound PowerContext Scope, record its exact revision, then pause with the authorized resume node. No formal Handoff for ordinary compaction. Git/approved Spec/local state outrank remote memory and chat; a conflict stops new implementation until reconciled. Maintenance requests route to `harness-maintain`, read-only checks to `harness-doctor`. Report truthful DONE/REPORT/PAUSED/CANCELLED/BLOCKED/RECOVERED boundaries and evidence paths.
