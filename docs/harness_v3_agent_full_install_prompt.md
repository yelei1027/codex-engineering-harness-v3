# Codex Engineering Harness V3.1.0 — Agent 全套安装主指令

你现在是 **Codex / Agent Harness 工程师、全栈工程师、DevOps 工程师和安全审计工程师**。你的任务是在当前机器上构建并验收一套 **Codex Engineering Harness V3.1.0**，最终做到：

- 一次安装、按项目启用；
- 日常用户只描述需求，由 Main Router 自动选择 Skill / MCP / Subagent；
- 同时保留 `@插件名`、`$skill-name`、`/plugins`、`/hooks` 等显式入口；
- 所有第三方组件使用可验证来源并锁定准确版本/commit/hash；
- 不重复注册 MCP / Hook / Skill；
- 不覆盖用户现有配置；
- 可以 `doctor`、更新、回滚、打包和迁移；
- 最终交付真实可复现的 Harness，而不是只生成文档或配置示例。

> 本次目标是安装与构建 Harness。允许在明确的受管目录、当前用户配置和用户指定的测试项目中创建/修改 Harness 所需文件；**不允许**删除整个 `~/.codex`、覆盖未知配置、删除业务仓库/数据、泄露凭证、推送 GitHub、合并业务 PR、部署生产环境。需要 sudo、账号登录、API Key、Hook trust、不可逆迁移或会影响现有全局环境的动作时，先显示准确动作和原因并等待用户完成/批准。

---

## 0. V3 不可违反的架构规则

### 0.1 唯一控制平面

`Main Router` 是唯一上层 Orchestrator。

默认自动开发链不得再嵌套另一套完整 Orchestrator：

- `implement`：允许安装，但**默认不自动触发**；
- `implement-spec`：允许安装，但**默认不自动触发**；
- 显式选择上游 Orchestrator 时，先读取锁定版本的实际副作用并确认提交、工作树、远端操作等权限，再进入 X00 独占执行；调用 Skill 本身不等于批准其中的所有副作用。V3 外层暂停重复的 implementation / review / commit 调度，但权限与证据门禁始终有效。完成后回 V00 验收同一候选；不得先执行、再补授权。

禁止出现：

```text
V3 worker DAG
  └─ implement-spec
       └─ 又创建另一套 worker/worktree/reviewer DAG
```

### 0.2 三档任务模式

Main 自动分类：

- **L0 LIGHT**：小修改、文档、局部配置、低风险一次性修复；
- **L1 STANDARD**：普通功能 / Bug；
- **L2 HIGH-RISK / COMPLEX**：权限/认证、数据库迁移、资金/计费、删除、并发、跨服务契约、多 Ticket、大型未知项目。

风险优先于 diff 大小。发现高风险后只能升级，不能无证据自动降级。

### 0.3 触发方式

默认：自然语言自动路由。

显式入口：

```text
@Codex Engineering Harness ...     # ChatGPT 支持 @ 插件选择的界面
$harness-dev ...                    # Codex 显式 Skill
$tdd ...
$code-review ...
/plugins                            # Codex 插件浏览/安装入口（运行时支持时）
/hooks                              # 审阅/信任 Hook，不是手动运行 Hook
```

如果当前 Codex 提供 `/skills`，记录为可选 Skill 选择器；如果不存在，标记 `UNSUPPORTED`，不要伪造。不要假设存在 `/skill` 单数命令。

MCP/Tool 不创建虚构 `$codegraph`、`$powercontext` 等命令；通过 Main 自动工具选择或自然语言明确指定。

Subagent 不是 Skill。`reviewer`、`researcher` 等必须是真实子任务/独立进程，不能由 Main 换口吻冒充。

### 0.4 Review 完整性

审查目标必须覆盖本任务已提交差异、staged、unstaged，以及相关 untracked 源码。

- clean committed range：以核验的 merge-base/fixed point 到 head 为范围；Matt code-review 的真实内部派发计入预算。
- dirty / mixed：不能只审 HEAD，也不能只运行 OCR workspace preview 就漏掉本任务已提交的部分。把提交差异与工作区差异统一到最终源码快照逐项审查。
- 先记录用户任务前已有修改的归属，不自动 stash/reset、全量 git add 或把无关变更纳入。
- 每次冻结创建新的 snapshot-id；保留实际源码内容，不仅记录路径。所有必需文件可读且版本稳定才算冻结成功。
- 输入 manifest/hash 只覆盖不可变输入；review-results 是追加结果，不改变冻结输入 hash。
- 用户要求 dirty 目标使用 Matt 时，先说明限制；只有获授权的隔离本地 review commit 或已验证适配才可使用，不能自动提交全部工作区。
- 代码、验收或依赖变化使受影响证据失效。仅 commit SHA 变化但内容/依赖/环境完全一致时，须有内容等价证明才能复用本地证据；CI 仍对齐最终 head/仓库合并候选。
- review_only 只返回报告，未执行检查标 UNVERIFIED，不自动实现或发布。

冻结结构：

```text
<review-root>/
└── <task-id>/
    └── <snapshot-id>/
        ├── target.json
        ├── input-manifest.json
        ├── committed.patch
        ├── tracked.patch
        ├── index.patch
        ├── untracked-manifest.json
        ├── source/
        │   └── <relative-source-paths>
        ├── spec-ref.json
        ├── checks.json
        ├── codegraph-summary.md
        └── review-results/
            ├── main-review.json
            ├── risk-review.json
            └── dispositions.json
```

`review-root=.harness-artifacts/review`。各文件职责以正式 HTML 第 08 节为准；排除秘密、数据库、会话和无关业务数据，特殊文件类型明确记录。

### 0.5 Hook / MCP 单一所有权

- 同一个组件只能有一个受管注册来源；
- Plugin route 与 standalone route 不可同时注册同一组 Hooks；
- 不假设同一事件多个 Hook 的执行顺序；
- 有严格先后依赖的动作必须进入同一个受管 handler，或改由 Main 显式调用。

V3 默认：

- planning-with-files：新项目默认使用 L2 安装 profile 并注册项目级 PWF Hook；任务风险仍独立按 L0/L1/L2 分类；显式选择 L1 时不注册这些 Hook；
- context-mode：默认 **MCP-only**，不要默认启用其生命周期 Hooks；
- PowerContext：MCP / 官方 Codex integration，用于正式 Handoff；
- CodeGraph：MCP；
- OCR：CLI + Delegation Skill；
- MarkItDown：MCP；
- screenshot-to-code：独立 UI Provider/sidecar，不是核心启动门槛。

---

## 1. 权威来源

在执行任何安装命令之前，先读取当前上游 README / release / 官方 OpenAI 文档，禁止依赖旧记忆或旧教程。

组件来源：

```text
https://github.com/mattpocock/skills
https://github.com/OthmanAdi/planning-with-files
https://github.com/mksglu/context-mode
https://github.com/oceanbase/powercontext
https://github.com/colbymchenry/codegraph
https://github.com/alibaba/open-code-review
https://github.com/microsoft/markitdown
https://github.com/abi/screenshot-to-code
```

OpenAI 当前文档至少核验：

```text
https://developers.openai.com/plugins/concepts/plugins
https://developers.openai.com/plugins/build/plugins
https://developers.openai.com/plugins/concepts/skills
https://developers.openai.com/plugins/build/skills
https://learn.chatgpt.com/docs/hooks
https://learn.chatgpt.com/docs/agent-configuration/subagents
```

如果上游文档与本指令的具体命令/字段冲突：

1. 先停止该组件安装；
2. 给出当前上游证据；
3. 保留 V3 的架构边界；
4. 选择当前运行时真正支持的实现方式；
5. 记录到 `compatibility.yaml` 与验收报告。

---

## 2. Phase 0 — 只读审计

先执行只读检查，不修改配置、不安装、不升级。

必须检测并保存：

```text
OS / kernel / arch
当前用户、HOME、shell
Git
Codex binary 的真实绝对路径
Codex --version
当前项目与 Git root（如有）
Node / npm / npx / pnpm
Python / python3 / uv / pip
Docker / docker compose
可用端口
现有 ~/.codex、项目 .codex、~/.agents、项目 .agents
现有 plugin marketplace
现有 plugins
现有 Skills 及同名来源
现有 MCP server 注册
现有 hooks.json / config.toml hooks
当前 Hook trust 状态（能读取时）
已有 PWF/context-mode/PowerContext/CodeGraph/OCR/MarkItDown/screenshot-to-code
当前工作树 dirty 状态
```

输出：

```text
.harness-install/audit.json
.harness-install/inventory.md
```

明确区分：

- NOT_INSTALLED
- INSTALLED_NOT_REGISTERED
- REGISTERED_NOT_VERIFIED
- VERIFIED
- CONFLICT
- UNKNOWN

如果发现旧 Harness 或重复插件，不要直接删除。

---

## 3. Phase 1 — 备份与所有权

创建本次安装事务 ID，例如：

```text
HARNESS-V3-INSTALL-YYYYMMDD-HHMMSS
```

建立：

```text
~/.local/share/codex-engineering-harness/
├── backups/<txn-id>/
├── transactions/<txn-id>/
├── cache/
├── runtimes/
└── reports/
```

在修改任何现有文件前：

- 原样备份；
- 记录 SHA256；
- 记录 mode/owner；
- 记录修改原因；
- 记录 rollback 操作。

禁止通过“目录看起来像 Harness”判断归属。

生成：

```text
manifests/ownership.yaml
```

未知来源内容一律视为用户内容，不能覆盖/删除。

---

## 4. Phase 2 — 创建 Harness V3 自身仓库

默认在用户可见的开发目录创建：

```text
codex-engineering-harness/
```

如果同名目录存在，先检查是不是本项目；不要覆盖。

至少创建：

```text
codex-engineering-harness/
├── VERSION                         # 3.1.0
├── plugin.json                     # portable manifest（当前 schema 支持时）
├── .codex-plugin/plugin.json       # compatibility manifest（当前 Codex 需要时）
├── skills/
│   ├── harness-dev/SKILL.md
│   ├── harness-doctor/SKILL.md
│   └── harness-maintain/SKILL.md
├── bin/harness
├── manifests/
│   ├── components.yaml
│   ├── compatibility.yaml
│   ├── ownership.yaml
│   └── licenses.yaml
├── templates/project/
├── adapters/
├── contracts/
│   └── workflow.json
├── scripts/
├── tests/
├── docs/
└── dist/
```

`harness-dev` 的 description 必须足够明确，让开发/修复/重构/Review 请求能够自动触发，但不要把闲聊、纯解释、普通非项目问题误触发。

Skill 里只保存 V3 路由规则，不复制整份上游 Skill 内容。

以交付的 `harness_v3_workflow_contract.json` 作为文档节点与分支契约，复制到实现仓库的 `contracts/workflow.json`。这是 Harness 设计数据，不是 Codex 原生 API。实际编排、事件日志和分支测试要与契约一致，不能把 JSON 字段直接当作未知的宿主工具参数。

正式 HTML 与契约应共同可读：HTML 单文件可打开，完整流程默认可见，每个节点有实际输入位置和调用示例。

---

## 5. Phase 3 — 安装 8 个组件

### 5.1 Matt Pocock Skills

目标：安装需要的 Skills 并锁定准确 revision。

必须先读取当前仓库结构与安装说明。

至少需要验证存在并记录实际来源/hash：

```text
grill-with-docs
wayfinder
codebase-design
research
to-spec
to-tickets
tdd
diagnosing-bugs
code-review
pr
retro
implement
implement-spec
```

规则：

- `implement` / `implement-spec` 安装但不进入 V3 默认自动路由；按目标运行时已核验的调用策略设置显式模式，不能只写一句 AGENTS 规则冒充强制保证；
- 对允许隐式调用的 Skills 测试实际匹配；用户显式方法请求不豁免任务/权限/证据门禁；
- `grill-with-docs` 的内部 grilling/domain-modeling 方法也须可加载，内部真实派发需计入预算；
- 对 `implement`、`implement-spec`、`code-review`、`tdd` 读取实际 `SKILL.md`，生成行为摘要和 hash；
- 如果 `implement` 仍自动 review/commit，必须在 compatibility manifest 标记；
- 如果 `implement-spec` 仍内部派发 worker/merger/reviewer，必须标记为 `UPSTREAM_ORCHESTRATOR`；
- 如果 `code-review` 仍内部派发 Standards/Spec 子任务，记录实际并发行为和 fixed-point 规则。

### 5.2 planning-with-files

先读取当前 Codex integration。

关键规则：

- Plugin mode 与 standalone `.codex/hooks.json` mode 二选一；
- 默认只安装能力，不在所有项目全局启用完整 PWF Hook；
- Harness L2 profile 才启用 PWF；
- 必须验证 `PLANNING_DISABLED=1`（如果当前版本支持），用于一次性 review/research/CI 进程；
- 不让子 Agent 写 Main 的 plan root；
- 重复 Hook 必须 doctor 报错。

如果当前上游推荐 plugin route，并且能做项目级 enable/disable，则优先使用 plugin route。

### 5.3 context-mode

目标：V3 默认只使用 MCP 大输出/索引能力。

先检查当前 CLI/package/plugin。

要求：

- 安装 CLI/server；
- **不要默认启用 context-mode bundled hooks**；
- 如果官方提供 MCP-only 安装/配置，使用它；
- 如果没有，按当前官方 server 启动方式只注册 MCP；
- 不重复手工注册已由 plugin 提供的 MCP；
- 验证 `ctx_execute` / `ctx_search` 等当前实际工具名，不靠旧文档猜；
- 记录版本、MCP server 名与工具列表。

### 5.4 PowerContext

先读取当前 README 与 Codex integration。

目标优先选择 **Basic memory / explicit Handoff**，除非用户已经提供并明确授权 Generation + Embedding API credentials。

要求：

- Python/uv 依赖使用受控环境；
- 服务目录、`.env`、数据库与业务仓库分离；
- `.env` 权限安全，不进入 Git；
- 官方 `setup codex` 运行前先比较它会修改哪些 Codex/Plugin/MCP 配置，避免重复注册；
- 新建测试 Scope；
- 验证 `ready`、`capabilities`、Codex doctor；
- 做一次真实 `Handoff → 新 Session/独立客户端 → Recall` 测试；
- 本次没有模型 API 时，不得宣称 Full automatic memory 已通过。

### 5.5 CodeGraph

先读取当前 README 和 `codegraph --help`。

注意：上游 `codegraph install` 可能自动修改多个 Agent 配置。

V3 规则：

- 安装 CLI 可以使用官方 installer/npm，但必须固定实际版本并记录校验；
- **不要无条件运行会修改所有 Agent 的 `codegraph install`**；
- 优先使用当前版本支持的目标 Codex-only 参数；若无此参数，则由 Harness 只注册一个 CodeGraph MCP source；
- 选一个小测试仓库运行 `codegraph init`；
- 验证 MCP 查询、调用链和索引自动更新；
- `.codegraph/` 属于项目派生数据，不进入 Harness 发行包。

### 5.6 OpenCodeReview

目标：采用 Delegation Mode，复用宿主 Agent，不配置第二个 LLM endpoint。

要求：

```text
ocr --version
ocr delegate preview --format json   # 当前版本支持时
ocr delegate rule --format json ...
```

安装/验证 `open-code-review-delegate` Skill。

必须测试：

- workspace mode（含 staged/unstaged/untracked）；
- range mode；
- 每个 reviewable file 必须 reviewed/skipped 有原因；
- 不把 OCR Delegation 当成独立 reviewer 本身；
- L2 的 reviewer 必须是真实独立上下文/独立进程。

### 5.7 MarkItDown

优先安装官方 `markitdown-mcp`。

要求：

- 使用独立 Python/uv 环境；
- 默认 STDIO 或 localhost；
- 不对局域网/公网开放；
- 验证实际 MCP 工具（当前 README 常见为 `convert_to_markdown(uri)`，以实际版本为准）；
- 用一个简单 PDF/Office/HTML fixture 做转换 smoke；
- 输出放 `.harness-artifacts/input/`。

### 5.8 screenshot-to-code

这是可选 UI sidecar，但要完成“已安装”状态；没有 Provider key 时允许 `PASS_WITH_OPTIONAL_PROVIDER_BLOCK`。

优先使用 Docker 隔离，避免污染系统 Python/Node；如果当前 Docker 路径不可用，再使用上游推荐的 Poetry + pnpm。

要求：

- backend/frontend 独立；
- `.env` 不进 Git；
- 至少一个 model provider key 才能做真实生成；
- 无 key 时只验证容器/前后端健康，不得宣称端到端生码通过；
- 有 key 时执行“上传测试截图 → 后端模型调用 → 返回代码 → preview”真实 smoke；
- 端口与 URL 写入 machine binding，不硬编码到可移植模板。

---

## 6. Phase 4 — V3 Plugin / Marketplace / 项目 Overlay

构建 `Codex Engineering Harness` 本地插件。

优先采用 OpenAI 当前 portable `plugin.json`；如当前 Codex 仍需要 compatibility manifest，同时提供 `.codex-plugin/plugin.json`。

不要伪造 schema。读取当前官方 schema/示例后生成并校验。

创建本地 marketplace（当前 Codex 支持时）：

```text
<repo>/.agents/plugins/marketplace.json
```

或个人 marketplace：

```text
~/.agents/plugins/marketplace.json
```

优先使用项目级 marketplace/enable，避免所有项目自动加载 Harness。

如果当前 CLI 支持：

```text
codex plugin marketplace add ...
codex plugin marketplace list
```

执行前用当前 `codex ... --help` 验证参数。

项目模板至少包含：

```text
harness.yaml
harness.lock
AGENTS.harness.md
.codex/config.toml fragment
.harness/task/
.harness/state/
.harness-artifacts/
```

合并 AGENTS.md 时：

- 先解析现有内容；
- 只追加一个带明确 begin/end marker 的最小 V3 区块；
- 不复制第三方长 README/AGENTS；
- 卸载时只移除该 marker 区块。

---

## 7. Phase 5 — 实现 `harness` CLI

至少实现：

```text
harness --help
harness install
harness enable <repo>
harness disable <repo>
harness start <repo>
harness status [repo]
harness doctor [repo] [--deep]
harness check
harness update --to <release>
harness rollback <transaction-id>
harness package
harness uninstall
```

### install

- 幂等；
- 不重复注册；
- 已存在但版本不同先进入 plan；
- 所有写操作进 transaction journal。

### enable

- 校验 Git root；
- 合并 Overlay；
- 设置项目 binding/profile；
- 不重装共享工具；
- 提醒 trust/new Session。

### start

- 解析真实 repo；从子目录启动时回溯到现有 Git root；
- 若当前目录尚未初始化且策略允许，首次 `start` 自动执行安全 `init + enable`，默认 profile=L2；
- 初始化拒绝 HOME、文件系统根、包含嵌套仓库的集合目录和 bare/异常 `.git`；
- 自动初始化不创建 commit、不创建 remote；enable 失败时保留用户文件与已创建 `.git`；
- 使用锁定/指定 Codex binary，并记录 `codex_launch_mode=desktop|cli`；
- Desktop bundled runtime 只用于 app-server / inventory / MCP 等运行时能力，不得冒充完整 TUI；此模式下 `harness start` 返回 `DESKTOP_PROJECT_READY` 和准确 repo，要求用户在 Codex Desktop 将该 repo 作为主文件夹并新建会话；
- 只有已验证的完整 standalone CLI 才允许 `exec` 启动，并从 repo root 建立新 Session；
- 不在错误目录旧 Session 内仅 `cd`。

### doctor

分开报告：

```text
STATIC
RUNTIME
TRIGGER
MCP
HOOKS
SUBAGENT
REVIEW
RECOVERY
ISOLATION
OPTIONAL_PROVIDERS
```

未执行真实测试必须 `UNVERIFIED`。

### update

- 不追逐每个组件 latest；
- 生成一个明确 release candidate；
- 对 Skill 行为变化做 diff；
- 隔离 candidate 验收；
- promote 前展示新增权限/迁移；
- accepted lock 原子更新。

### rollback

- 只恢复 transaction touch set；
- 不覆盖事务开始前已有 dirty changes；
- 不把二进制降级冒充数据库回滚。

### package

排除：

```text
.env
API keys/tokens
SSH/Git credentials
Memory DB
context-mode DB/cache
真实 PWF/用户 task state
聊天记录
业务数据
业务仓库
机器 binding
```

生成 manifest、SHA256SUMS、第三方 notices、acceptance summary。

---

## 8. Phase 6 — Main Router 与自动触发验收

创建至少以下测试用例。

### 8.1 自动 Skill 正例

自然语言：

```text
为当前项目增加 CSV 导入，重复 SKU 要报错并保留错误行。
```

期望：

- 自动进入 `$harness-dev` 或等价 Harness Router；
- 自动分类 L1 或根据真实项目风险升级；
- 不要求用户手动输入 M01/Dxx 等节点。

### 8.2 自动 Skill 负例

```text
解释一下 Python list 和 tuple 的区别。
```

期望：Harness 项目开发 Skill 不强行启动完整开发流程。

### 8.3 `@` 插件入口

仅在当前界面支持且插件/Skill 已实际安装、可从 @ 选择器选中时：

```text
@Codex Engineering Harness 检查这个项目的测试失败并修复。
```

实际验证所选条目被加载。Linux 本地安装不等于 ChatGPT 网页已自动获得同名插件；不能测试该界面时标 UNVERIFIED，不伪造成功。

### 8.4 `$` Skill 入口

```text
$harness-dev ...
$tdd ...
$code-review ...
```

验证实际加载来源/hash。

### 8.5 `/` 入口

验证：

```text
/plugins
/hooks
```

按官方文档核对 Codex CLI/IDE 的 `/skills` 与 `$` 入口；在目标运行时逐项实测。`/plugins` 是插件管理，`/hooks` 是审阅/信任，不是运行全部 Hook。不存在的界面入口标 UNSUPPORTED；不可访问的界面标 UNVERIFIED，不阻塞不依赖它的核心功能。

### 8.6 MCP 自动与显式

自动：大型日志应选择 context-mode。

显式：

```text
使用 CodeGraph 分析这个服务的调用链。
```

不得出现虚构 `$codegraph`。

### 8.7 Subagent

验证 L0 小任务不乱派子 Agent。

验证 L2 风险任务实际启动独立 reviewer；必须取得运行回执/child ID/等价证据。

---

### 8.8 完整流程与唯一主编排验收

逐项实现并验证正式图中的条件路径，不把图上所有节点当作每次必跑：

- A00/D00：新任务、Review-only、恢复、维护、纯解释分流；纯解释不运行开发链。
- A01/D01：当前 Session/runtime、用户已有修改归属、必要能力真实可用。
- D02/I01/I02/R00：外部输入是多选；汇合只等待所选必需项。
- D03—U01：按需路线、仓库/外部取证、澄清、接口、原型、验收与范围授权。
- S01→R01、S06→R06：补证从 B04 发起则回 B04；维护补证回 U11，不丢 resume_node。
- D10—D14：单任务或 Ticket，选择 Main/worker，不提前派发；显式上游 Orchestrator 走 X00，外层不并发另一套调度。
- S02→R02：独立验收测试仅写批准测试范围；不等于写完全部 TDD 后再交实现。
- S05：worker 只运行一次 T00—D18 子流程，完成后回 R05；Main 不重复执行同一份实现。
- T00/T01/D16/T02/T03/D17：一个行为一次实际 RED→最小实现→GREEN；无效测试/环境故障有独立返回。
- T05：非行为任务先实际修改，再做适用检查，不能跳过修改节点。
- D18：Main 自执行进入 C02，child_id=null；worker 返回 R05。
- C02/D15/V00/D19：整合必要 Ticket，再验证同一候选；不使用各 worker 局部通过冒充集成通过。
- E00—D23：完整冻结范围、主审查、S07/R07 内部两轴、必要 S04/R04 独立审查、问题分类处置。
- P00—P07：本地/PR/合并/部署是不同出口，CI 对齐最终目标；每项远端动作与发布权限单独核验。
- F00—F04：代码、环境、权限、证据、可选服务和应用恢复分开；应用失败不触发 Harness 更新。
- H00—D30：保存真实检查点，核验后恢复；提交/推送/合并/部署先重新经过 U02/U03/U04，不恢复旧许可直接执行。
- U10—U16：检查→计划→批准→候选→激活验收；成功接受，只有触碰正式环境后失败才进入相应事务恢复。

### 8.9 正式 HTML 和目录排版验收

保留正式 V3 风格，不添加版本修订记录。完整图不能缩成只有六至八个阶段的摘要。

- 页面同时有简明主线总览、完整节点/决策分支、失败返回、七个实际委派点和接收点。
- 桌面端目录必须固定在页面左侧并随正文滚动保持可见；当前阅读章节/流程阶段自动高亮。左侧目录可在自身高度不足时独立滚动；正文不得因此出现嵌套滚动。窄屏可切回顶部/普通流布局。
- 每条分支有条件、解释、目标链接；可选分支与顺序步骤不能混画。
- 目录和代码示例必须保留换行、缩进。HTML 使用正确预格式化结构，不能把目录树放进会折叠换行的普通 div。
- 冻结证据、Harness 源码、业务项目、机器私有状态与发行产物分开显示；注释放在说明中，避免靠空格对齐中英文长注释。
- 主内容随页面自然滚动；不添加中间滚动区，不添加放大/缩小/适配宽度/重置工具栏。
- 校验节点 ID 唯一、所有本地锚点有效、复制按钮保留真实换行、必要节点可达、动态恢复有白名单。
- 检查 1440/1024/768/390/360 像素宽度下无页面横向溢出；实际查看目录树、分支和子任务节点渲染。
- HTML/路由结构验证与本机 Runtime/MCP/Hook/Subagent 测试分开报告，前者不能冒充后者。

---

## 9. Phase 7 — Review 完整性测试

准备测试 repo，分别验证：

1. clean branch commit range；
2. staged change；
3. unstaged change；
4. untracked source file；
5. `.env` / secret fixture 不进入 evidence；
6. worker 集成后 review target 更新；
7. review 后代码发生变化，旧 review 自动标记 stale；
8. 已提交变更＋staged＋unstaged＋untracked 的混合目标全部覆盖；
9. staged 删除后 untracked 同路径重建、重命名、二进制/子模块有明确处理；
10. 新增报告不改变冻结输入 hash，修改源码必须产生新 snapshot-id；
11. review_only 不自动变成实现、提交或发布；
12. SHA 变化但内容等价的证明，与代码变化导致的证据失效分开。

对于 Matt `code-review`：

- 记录实际 fixed point；
- 记录内部 Subagent 数；
- dirty workspace 不得标记 Matt review full coverage。

对于 OCR Delegation：

- workspace/range 结果与最终真实任务 allowlist 一致；混合目标不能漏掉已有任务提交；
- 每个文件 reviewed / skipped；
- skipped 必须有原因。

---

## 10. Phase 8 — Hook/MCP 冲突测试

至少检查：

- PWF plugin + PWF standalone 同时存在时 doctor 必须 FAIL；
- context-mode plugin hooks 若意外启用，doctor 必须提示与 V3 MCP-only profile 不一致；
- 同 MCP endpoint 被两处重复注册必须 FAIL；
- project A / project B / 未启用 project 三组测试；
- 未启用项目不得加载 Harness 项目路由或 PWF plan；
- Hook trust 未批准时必须显示 `PENDING_TRUST`，不是 PASS；
- 同事件多个 Hook 不写“执行顺序保证”。

---

## 11. Phase 9 — 跨 Session / 恢复测试

L1：

- 写本地 task state；
- 新 Session 对照 Git + task state 恢复。

L2：

- PWF plan；
- PowerContext Handoff；
- 新 Session 使用同项目 Scope Recall；
- 将 Recall 与当前 Git/Spec 交叉核验；
- stale 内容不能覆盖当前事实。

PowerContext unavailable 时：

- 本地开发仍可继续；
- 正式 remote Handoff 标为 `UNSYNCED`；
- 不用本地文件冒充 MCP Recall 成功。

---

## 12. Phase 10 — 更新/回滚故障注入

至少模拟：

- 某组件下载失败；
- Hook schema 改变；
- MCP server 启动失败；
- candidate smoke 失败；
- promote 后 doctor 失败；
- rollback 恢复。

旧 accepted 环境必须在 candidate 阶段保持可用。

---

## 13. Phase 11 — 最终锁定与打包

生成：

```text
harness.lock
manifests/components.yaml
manifests/compatibility.yaml
manifests/ownership.yaml
manifests/licenses.yaml
reports/install-report.md
reports/doctor.json
reports/acceptance-summary.json
reports/known-limitations.md
reports/document-validation.json
reports/routing-validation.json
contracts/workflow.json
```

每个组件至少记录：

```text
name
source URL
resolved tag/commit/package version
content hash / checksum（可获得时）
install route
registration owner
MCP servers
hooks
skills
sidecars
credentials required
project scope/global scope
smoke result
accepted status
```

然后执行：

```text
harness package
```

在一个干净测试环境完成：

```text
解包
→ install
→ enable test-project-A
→ start / doctor
→ 自动触发 smoke
→ 显式 @ / $ / / smoke（适用界面）
→ MCP smoke
→ L0/L1/L2 路由 smoke
→ dirty workspace review smoke
→ disable
→ uninstall
→ 再次 install 验证幂等
```

只有在真正断网环境包含全部依赖并通过安装，才标记 `OFFLINE_INSTALL_VERIFIED`；否则统一称“可复现在线安装包”。

---

## 14. GitHub 边界

完成本地仓库和发行包，但**不要自动 push 到 GitHub**。

准备：

```text
.gitignore
README.md
LICENSE（仅 Harness 自身，第三方许可证另列）
THIRD_PARTY_NOTICES
SECURITY.md
CONTRIBUTING.md（可选）
release checklist
```

向用户报告“已具备发布条件”，等待用户明确授权后再创建/推送远程仓库。

---

## 15. 最终回报格式

安装结束后，不要只说“完成”。必须返回：

```text
1. Harness V3 状态：PASS / PASS_WITH_OPTIONAL_BLOCK / FAIL / BLOCKED
2. Codex 真实 binary + version
3. Harness repo 路径
4. 8 组件逐项：版本 / 安装方式 / 注册方式 / 验收状态
5. Hooks：来源、事件、trust、重复检查
6. MCP：server + tools + smoke
7. Skills：实际来源/hash + 自动触发测试
8. Subagent：独立委派 smoke
9. Review：clean + dirty workspace 覆盖结果
10. PWF / PowerContext 恢复测试
11. screenshot-to-code Provider 状态
12. harness CLI 命令实测结果
13. package 路径 + SHA256
14. 已知限制 / 人工待办
15. 完整文档/分支结构/排版验收与 Runtime 实测分别报告
16. 未执行的测试明确写 UNVERIFIED
```

如果某个组件因为 API Key、账号授权、Hook trust 或系统权限暂时不能完成：

- 继续完成不依赖它的部分；
- 不要伪造 PASS；
- 最终使用 `PASS_WITH_OPTIONAL_BLOCK` 或 `BLOCKED`；
- 给出最小人工动作和完成后应重新执行的具体 smoke。

---

## 16. 执行纪律

从现在开始直接执行 Phase 0，只读审计。

不要先让我手工逐条运行八个组件安装命令；你是安装 Agent，应连续完成所有已授权、可自动执行的工作。

不要为了“全套安装”而降低安全边界：

- 不覆盖未知配置；
- 不重复 Hook/MCP 注册；
- 不把工具启动当功能验收；
- 不把 Main 自审当独立 reviewer；
- 不把旧 review/CI 结果用于新代码版本；
- 不把可选 UI Provider 故障升级为整个 Harness 核心失败；
- 不把业务数据或凭证放进发行包。

每完成一个阶段，把真实证据写入安装报告；如果遇到需要用户动作的唯一阻塞点，明确展示“需要做什么、为什么、完成后从哪个命令继续”。
