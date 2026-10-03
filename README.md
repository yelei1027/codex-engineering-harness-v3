# Codex Engineering Harness V3.1

面向 Codex / Vibe Coding 的项目级开发 Harness。
核心目标：**一台机器安装一次，每个项目一条命令启用；Skill、MCP、Hook、Subagent 按需自动路由。**

> 当前稳定版：`v3.1.0` · Linux x86_64 · Codex Desktop
> 正式状态：`PASS_WITH_OPTIONAL_BLOCK`（核心能力已通过；可选模型 Provider 不影响日常开发）

## 核心能力

| 能力 | 状态 |
| --- | --- |
| 项目级 Skills / Main Router | 可用 |
| context-mode / CodeGraph / MarkItDown / PowerContext Basic | 实际调用通过 |
| L2 项目 Hooks | 10 个 Hook，信任与原生生命周期验证通过 |
| `/compact` 后恢复 | `PostCompact → RESUME_OK` 已验证 |
| TDD / Review / Subagent | 按任务风险调用 |
| PowerContext Full 自动记忆 | 可选，默认未配置 |
| screenshot-to-code 第三方模型 Provider | 可选，默认未配置 |
| Codex 原生截图理解 | 默认视觉输入路线 |

---

## 1. 新机器安装

### 前置环境

请先安装：

- Git
- Node.js / npm
- Python 3
- `uv`
- Docker + Docker Compose v2
- Codex Desktop
- GitHub CLI `gh`

这是私有仓库，首次在新机器上先登录：

```bash
gh auth login
```

### 一条命令安装

```bash
gh api repos/yelei1027/codex-engineering-harness-v3/contents/scripts/install-release.sh --jq .content | base64 -d | bash
```

这条命令会自动完成：下载正式 Release → SHA256 校验 → 解包到独立安装目录 → 安装锁定依赖 → 启动本地服务 → 注册 `harness`。

默认安装目录：

```text
~/.local/share/codex-engineering-harness/releases/3.1.0
```

安装完成后验证：

```bash
harness --help
harness status
```

如果当前 shell 找不到 `harness`：

```bash
export PATH="$HOME/.local/bin:$PATH"
```

> 不要把 Harness 解压或安装到某个业务项目目录里。Harness 是机器级控制平面，业务项目只放项目 Overlay。

---

## 2. 第一次在项目中启用

进入真实项目根目录：

```bash
cd "/你的项目路径"
harness start .
```

首次启动会自动识别 Git 根、必要时初始化本地 Git、默认以 L2 profile 启用项目，并返回 `DESKTOP_PROJECT_READY` 和准确项目路径。

然后在 **Codex Desktop** 中把这个项目目录作为**主文件夹**打开，并新建会话。

L2 项目第一次使用时输入：

```text
/hooks
```

核对并信任项目 Hook。Hook 定义发生变化时，Codex 可能再次要求信任。

### 开始开发

平时直接描述需求即可：

```text
按本项目 Harness 开发：
为供应商管理增加 CSV 导入，支持字段映射、重复 SKU 检测和错误行提示。
按任务风险自动判断 L0/L1/L2，不需要的 Skill、MCP 和 Subagent 不要调用。
```

需要显式强制进入 Harness Router：

```text
$harness-dev
```

其他 Skill 也可显式调用，例如 `$tdd`、`$code-review`。

MCP 不使用 `$` 命令，直接自然语言指定即可：

```text
使用 CodeGraph 分析 SupplierService 的调用链。
```

```text
使用 PowerContext 保存当前正式 Handoff。
```

Hook 自动按生命周期触发；`/hooks` 只用于查看、启用和信任。

---

## 3. 在其他项目中使用

Harness 本体**不需要重复安装**。

每个新项目只需要：

```bash
cd "/另一个项目路径"
harness start .
```

然后在 Codex Desktop 以该项目为主文件夹打开新会话。

如果不需要 L2 生命周期 Hook，可使用 L1：

```bash
harness init --profile L1
```

## 4. 常用命令

| 操作 | 命令 |
| --- | --- |
| 当前项目初始化/启用 | `harness start .` |
| 显式初始化 L2 | `harness init --profile L2` |
| 无 Hook 模式 | `harness init --profile L1` |
| 检查当前策略 | `harness defaults` |
| 深度诊断 | `harness doctor . --deep` |
| 更新项目 Overlay | `harness refresh .` |
| Context 状态 | `harness context status .` |
| 保存 Handoff | `harness context handoff . --task TASK_ID` |
| 恢复 Handoff | `harness context recall . --task TASK_ID` |
| 禁用某个项目 | `harness disable "/项目路径"` |
| 卸载共享 Harness | `harness uninstall` |

---

## 5. 更新

### 只更新某个项目的 Harness Overlay

```bash
harness refresh .
```

然后重新打开 Codex Session；如果 Hook 定义变化，使用 `/hooks` 重新审阅和信任。

### 更新 Harness 本体

Harness 不允许用 `install` 直接覆盖已接受版本。正式更新使用隔离 candidate：

```bash
harness update --help
```

更新链路：

```text
下载新 Release
→ 独立候选目录
→ harness update --to <候选目录>
→ 验收
→ promote
→ 对已启用项目执行 harness refresh
```

不要删除 `~/.codex`，也不要手工覆盖 `accepted.json`。

## 6. 卸载

先禁用已启用的项目：

```bash
harness disable "/项目A路径"
harness disable "/项目B路径"
```

然后卸载共享 launcher：

```bash
harness uninstall
```

如需同时停止本地 sidecar 服务，在 Harness 安装目录执行：

```bash
python3 scripts/services.py stop
```

停止服务不会主动删除 PowerContext 数据卷或其他持久数据。

---

## 7. Context 满了怎么办

同一任务继续时：

```text
保存 checkpoint
→ /compact
→ PostCompact 核验
→ RESUME_OK
→ 继续
```

跨 Session 时使用正式 Handoff。当前默认采用：

- Git：代码事实
- Task / PWF：执行状态
- Harness Checkpoint：Compact 恢复
- PowerContext Basic Handoff：跨 Session 恢复

PowerContext Full 自动 Memory、Embedding / Hybrid Search 是可选增强，不是核心依赖。

---

## 8. 正式资料

- [完整架构与流程手册](docs/codex_engineering_harness_v3_parallel_flow.html)
- [安装与发行说明](INSTALLATION.md)
- [Context 恢复说明](skills/harness-dev/references/context-recovery.md)
- [正式验收摘要](reports/acceptance-summary.json)
- [已知限制](reports/known-limitations.md)

### Release

当前正式包：

```text
v3.1.0
codex-engineering-harness-3.1.0-linux-x86_64.tar.gz
SHA256:
cac560ce0bedb1c31fab33a51e9689d71234593ded694ceeda8f7b53e262063c
```

仓库与 Release 均为 Private。
