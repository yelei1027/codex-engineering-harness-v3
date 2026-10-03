For project development, debugging, refactoring or review, read the project-local
`.agents/skills/harness-dev/SKILL.md` and follow its Main Router. Keep one main
control plane. Choose L0/L1/L2 by risk; L2 uses independent reviewers and formal
handoff. Explicit skills still obey permission, frozen-input review and evidence
gates. Questions that only request explanations do not start the development chain.
Read `harness.yaml` and `.harness/state/project.json` for this project's binding.
Use the bound Harness CLI for task events and immutable review snapshots.
L2 is the default installation profile; choose task risk independently. Project
Hook definitions still require native user trust. At milestones and before
compaction, save a local context checkpoint. After compaction or a new session,
verify current Git, approved Spec, task/PWF and test/review evidence before edits.
Use current Codex image understanding for screenshot work. Save small task
summaries to the project's PowerContext Scope for formal handoffs; never export
login tokens or store credentials/transcripts in memory.
