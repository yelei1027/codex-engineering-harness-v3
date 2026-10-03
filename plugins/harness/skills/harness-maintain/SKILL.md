---
name: harness-maintain
description: Install, update, repair, roll back, package, migrate, enable, or disable
  Codex Engineering Harness with component provenance, isolated candidates and transaction
  recovery. Use for Harness maintenance requests.
---

# Harness Maintenance

Follow U10–U16 in [the workflow contract](references/workflow.json); this is design data, not a Codex native API. Resolve the real Harness/runtime paths and read `bin/harness --help` before using actual maintenance commands. Start with read-only inventory, accepted/candidate locks, ownership and active project consumers; avoid guessed latest versions and unknown global changes.

Build a concrete plan with pinned source/version/hash, dependencies, skill behavior/policy changes, project impact, touched files, candidate isolation, checks and recovery boundaries. Apply within existing user authorization; request only missing material permission, login, key, hook trust, sudo or irreversible migration approval. Never treat a skill mention as permission for all bundled side effects.

Use independent candidate dependencies/config/ports/test data. Back up each existing resource before mutation, record hashes/mode/ownership and a transaction journal; do not overwrite unknown user content. Static validation cannot substitute for actual component calls, model routing, complete snapshot review or enabled/disabled project verification. Promote only after required candidate checks and safe activation in the right new session; update accepted lock only for truly accepted tested state, not read-only checks. Do not replace dependencies used by an active encoding session. If the runtime cannot guarantee safe promotion, report blocked scope accurately.

Use actual CLI rollback for the transaction's touched set, preserving unrelated later configuration and user data, then reverify the old environment. Package only managed reproducible artifacts and pinned sources; exclude credentials/session stores/DBs/unrelated data. Application deploy/database recovery remains separate. Preserve one registration source per component: skills plugin OR standalone skills, project MCP config OR plugin MCP, never duplicate hooks. Default PWF capability is skill-only; explicit L2 project profile may add one PWF hook handler only after trust. Keep context-mode MCP-only, PowerContext explicit handoff, OCR CLI+delegation; no global lifecycle hooks. Report real installed/registered/verified/unsupported states and incomplete external prerequisites.
