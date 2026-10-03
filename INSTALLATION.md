# Codex Engineering Harness 3.1.1 — Installation & Release Guide

## 最快安装（新机器）

先完成 `gh auth login`，然后执行：

```bash
gh api repos/yelei1027/codex-engineering-harness-v3/contents/scripts/install-release.sh --jq .content | base64 -d | bash
```

安装完成后，每个项目只需要：

```bash
cd "/你的项目路径"
harness start .
```

详细说明见仓库首页 README。已有 Harness 不要重复运行安装脚本；版本更新走 `harness update` 候选验收流程。

## 1. Install the prepared control plane

```bash
cd /path/to/codex-engineering-harness
python3 scripts/bootstrap.py
# Optional local sidecars:
python3 scripts/bootstrap.py --services
./bin/harness install
./bin/harness doctor --deep
```

`bootstrap.py` prepares locked dependencies. `harness install` registers the accepted source; it does not make untrusted project Hooks trusted.

## 2. Enable or initialize a project

Recommended default for a new project:

```bash
cd /absolute/path/to/project
harness init
harness start
```

`init` and first `start` may initialize a local Git repository and enable Harness with the default L2 installation profile. They do not create a commit or remote. Existing Git roots are resolved from subdirectories. HOME, filesystem root, bare/abnormal repositories, and collection directories containing nested repositories are refused.

With the Codex Desktop bundled runtime, `harness start` returns `DESKTOP_PROJECT_READY` and the exact repository path instead of trying to launch a TUI. Open that repo as the **primary folder** in Codex Desktop and start a new chat. A verified complete standalone Codex CLI can use `HARNESS_CODEX_BINARY` with `HARNESS_CODEX_LAUNCH_MODE=cli`.

To avoid project lifecycle Hooks:

```bash
harness init --profile L1
```

For an already enabled project after a Harness source update:

```bash
harness refresh /absolute/path/to/project
```

Refresh preserves the selected profile, unmanaged AGENTS/config additions, and task data.

## 3. Trust L2 Hooks

Open a **new Codex session from the enabled project**, then use:

```text
/hooks
```

Review source, matcher, resolved command and requested behavior. Trust only the expected project-owned definitions. Direct execution of Hook scripts is not equivalent to native lifecycle delivery.

The L2 project currently combines locked planning-with-files handlers with Harness context handlers for `PreCompact`, `PostCompact`, and `SessionStart`. Same-event handlers must not rely on ordering.

## 4. Context recovery

```bash
harness context status . --used-percent 80
harness context checkpoint . --task TASK --facts .harness/task/TASK/context-facts.json --resume-node T00
harness context verify . --task TASK
harness context handoff . --task TASK
harness context recall . --task TASK
```

70/80/90% are policy thresholds applied only to a real usage number supplied by the host/user. Ordinary `/compact` creates a local checkpoint, not a formal remote Handoff. Cross-session Handoff uses the project-bound PowerContext Basic Scope and exact revision. Full automatic memory remains optional and requires separate supported model providers.

## 5. Visual input

For screenshot/UI work, prefer the current Codex image-understanding route when available. The local screenshot-to-code frontend/backend remains an optional sidecar. Its health does not prove third-party model generation; model-provider credentials must be configured and separately tested.

## 6. Release verification

Before packaging:

```bash
python3 -m pytest tests/test_harness_core.py -q
./bin/harness doctor --deep
git diff --check
git status --short
```

Required release blockers: current source tests, review/secret audit, documentation/version consistency, package clean-install validation, and L2 native Hook lifecycle verification if L2 is claimed as fully verified. Optional provider blocks must remain explicit.

Package only through `./bin/harness package`, then extract the archive into a clean directory and verify install → enable/init → start/doctor → disable/uninstall → reinstall. Do not publish machine bindings, secrets, databases, task state, caches, or business data.
