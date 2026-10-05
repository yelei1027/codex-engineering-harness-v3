# Context checkpoints and provider routing

First `harness start` previews the current project's preparation without writing
configuration or initializing Git. After approving that concrete scope, use
`harness prepare --apply` (or explicitly authorized `harness init`); preparation
defaults to L2. Bind one task/PLAN_ID per Main session. Explicit `--profile L0` or
`--profile L1` opts out of hooks; installation profile and task risk are independent.
Review new/changed definitions through native `/hooks`; registration is not trust.

Use `/status` where the host supports it. At real reported usage of 70%, refresh
task/PWF state; 80% requires checkpoint before new work; 90% permits only recovery
work. These are local policy thresholds. If usage is unavailable, say UNKNOWN and
checkpoint at milestones/before compact. Do not parse/copy full transcripts to
guess percentages. `harness context status . --used-percent 80` evaluates a real
host reading supplied by the caller; it is not an autonomous token monitor.

Before compact, update the selected `.planning/<plan-id>/task_plan.md`,
`findings.md`, and `progress.md` when PWF is used. Record a concise JSON summary
in `.harness/task/<task-id>/context-facts.json` with exactly these fields:

```json
{
  "objective": "Current approved objective",
  "completed": ["Verified completed work"],
  "remaining": ["Unfinished work"],
  "blockers": [],
  "decisions": ["Design decisions and rationale"],
  "next_action": "Exact next tool/action",
  "spec_refs": ["docs/spec.md"],
  "tests": [{"command": ["python3", "-m", "unittest"], "exit_code": 0}],
  "review": {"status": "UNVERIFIED", "snapshot_id": null},
  "workers": []
}
```

Examples describe schema, not evidence. Include only tests actually run; tests
may include an `evidence` path relative to the repo. Include approval identifiers
in local Spec/Ticket evidence, actual review snapshot/status, and real child/run
IDs with `running`, `pending`, `completed`, `stopped`, or `failed` status. Do not
invent results or mark unknown workers complete. Missing facts prevent READY.

```bash
harness context checkpoint . --task TASK --facts .harness/task/TASK/context-facts.json --resume-node T00 --session ACTUAL_SESSION_ID
# When using PWF, bind its selected ID explicitly (it may differ from TASK):
harness context checkpoint . --task TASK --facts .harness/task/TASK/context-facts.json --plan-id PLAN_ID --resume-node T00
# Same objective: use native /compact, then:
harness context verify . --task TASK
```

`--plan-id` selects only `.planning/<ID>/` in this repository and requires all
three planning files. The checkpoint preserves that binding for later manual
and automatic checkpoints. Without the selector, a verified same-project/task
checkpoint retains its binding; otherwise the legacy `.planning/<task-id>/`
path applies. Host environment selectors and shared active-plan pointers do not
select checkpoint references. Legacy callers may omit planning files.

Automatic fact reuse requires unchanged Git, branch, workflow and recorded
Spec/test/PWF hashes, including Git-ignored evidence. A changed reference or
plan binding produces `CHECKPOINT_PARTIAL`; provide fresh facts before resuming.

Choose a resume node allowed by the contract. The checkpoint captures Git
branch/HEAD (or an empty tree before the first commit), staged/unstaged/untracked
paths, workflow revision, Spec/test evidence hashes and PWF references. It copies
no credentials or source code. Semantic/test/review claims remain agent-recorded;
Main must cross-check evidence and authorization. On success report
CHECKPOINT_READY, resume node, HEAD and unfinished work. Hooks create only a
mechanical fallback when facts are stale; CHECKPOINT_PARTIAL needs Main completion.
PreCompact never creates a formal remote Handoff. PostCompact detects drift;
SessionStart reminds Main to recover before editing. These handlers need trust.

After `RESUME_OK`, Main checks actual workers, approved Spec and test/review
evidence, then continues. On RESUME_CONFLICT stop new implementation and reconcile
current Git/worktree → approved Spec/Ticket → local task/PWF → PowerContext → chat.
Never restore older code from a chat or Handoff. Two compactions recommend a
formal Handoff, rather than endlessly repeating compact.

For a new Main session, wait for or stop actual workers, save a fresh checkpoint,
then `harness context handoff . --task TASK`. The CLI uses the already bound
loopback PowerContext service, creates a stable isolated project Scope, stores
only objective/completed/remaining/decisions/blockers/next action, and verifies an
exact committed artifact/revision. Repeating the same checkpoint is idempotent.
UNSYNCED preserves local recovery. Then use `task pause` with the real resume node.
In the new session, `context recall` reads that exact revision as untrusted
history; cross-check current state before `task resume`. Pausing changes workflow
revision: create a final local checkpoint after pause before restoring that state.

Default screenshot route: inspect the user's image using native Codex image
tools, implement in the authorized project, and verify the rendered result. This
uses the current session's model access without a sidecar API key. PowerContext
Basic stores summaries prepared by this Codex session; it does not imply Full
background extraction, embeddings, or every-prompt capture. Neither sidecar has
automatic access to a ChatGPT API key. Their direct provider route requires
separate configuration or an actual supported app-specific OAuth integration.
Never read/export `auth.json` or reinterpret subscription tokens as API keys.
