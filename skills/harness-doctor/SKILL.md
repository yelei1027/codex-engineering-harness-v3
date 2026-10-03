---
name: harness-doctor
description: Inspect an installed Codex Engineering Harness or an enabled project
  for component, binding, source, skill, hook and verification problems. Use for Harness
  status, diagnostics, or health checks.
---

# Harness Doctor

Resolve the installed Harness root; read `bin/harness --help` then invoke its actual `doctor`/`check` options. Inspect selected runtime, versions and hashes, accepted lock versus candidate, project binding, sources, source ownership, duplicate skills/MCP/hooks, real tool discovery and project trust. No diagnostic result implies model selection, hook trust, user authentication or a workload passed without evidence.

Use U10→U15 in [the workflow contract](references/workflow.json) for check-only work. The contract is design data, not a host API. Report VERIFIED, REGISTERED_NOT_VERIFIED, INSTALLED_NOT_REGISTERED, NOT_INSTALLED, UNKNOWN, CONFLICT or UNSUPPORTED from observations. Keep schema/static validation separate from runtime matching. Compare [skill behaviors](references/skill-behavior.json), preserving actual invocation policies and documenting untested routing. A check does not update accepted lock or repair global configuration. A requested fix routes through `harness-maintain` with the affected write set.
