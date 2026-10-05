# Codex Engineering Harness

面向 Codex / Vibe Coding 的项目级开发 Harness。
目标：完整组件一次准备；明确说“用 Harness”后进入当前项目流程，模糊需求先引导，明确且已授权任务直接推进。

> 当前发行版：`v3.2.0` · Linux x86_64 · Codex Desktop
> 本版按用户决定正式发布，仍保留明确的未验证项；发布不等于完整 PRD 验收通过。请先阅读 [发行说明与已知限制](docs/RELEASE-3.2.0.md)。

## 核心能力

| 能力 | 状态 |
| --- | --- |
| 项目级 Skills / Main Router | 原有能力；本轮显式启动行为仍在验收 |
| context-mode / CodeGraph / MarkItDown / PowerContext Basic | 既有版本有调用证据；本轮整合需重新核验 |
| L2 项目 Hooks | 每个项目需原生信任；定义和实际生命周期分别核验 |
| `/compact` 后恢复 | 既有版本有恢复证据；以当前源码、计划及证据为准 |
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
- 可用的 Linux 用户级 systemd（用于管理 Harness 启动的组件进程）
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
~/.local/share/codex-engineering-harness/releases/3.2.0
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

首次 `start` 返回 `PREPARATION_REQUIRED`，只展示项目接入影响，保留原文件。确认 Git、配置、技能、权限默认值和 Hook 影响后执行：

```bash
harness prepare "/你的项目路径" --apply
```

准备默认使用 L2 安装配置；已有 Git 不创建提交或改远端。已有效接入项目不重复初始化。你可以从任意目录传入项目的绝对路径，无需进入 Harness 安装目录。

然后在 **Codex Desktop** 中把这个项目目录作为**主文件夹**打开，并新建会话。

首次权限步骤集中处理：核对原生项目加载/信任提示后，在消息框下方的权限菜单选择项目准备提供的 `harness-inspect`（Harness 需求检查：只读），再查询新一轮实际权限。桌面保存的权限选择可能覆盖配置默认值；设置页显示只读不能证明聊天已切换。若菜单未显示该配置，记录缺失并停止保护阶段，不反复寻找内置只读按钮。

具体修改范围确认后，在原生菜单选择“请求批准”开放工作区写权限，下一轮核验后范围内自主实现。完成或取消后恢复 `harness-inspect` 并再次核验。工作区写权限覆盖工作区，业务文件写集仍须遵守确认范围；MCP/App 副作用另外审核。Harness 不会自动改写桌面权限选择或 Hook 信任。

L2 项目第一次使用时输入：

```text
/hooks
```

核对并信任项目 Hook。Hook 定义发生变化时，Codex 可能再次要求信任。

### 开始开发

安装共享入口并在新会话加载后，明确说“用 Harness”才激活；同一聊天后续不用重复名称。新项目先说明并确认一次接入范围，然后进入主路由。普通问答和未请求 Harness 的开发任务保持普通流程。

模糊请求示例：

```text
用 Harness 优化这个项目。先给我只读体检和少量建议，确认具体范围后再实现。
```

系统只询问影响目标、验收或风险的关键取舍；代码里可查的事实由 AI 自行检查。已授权范围内的实现、测试修复和审查自主推进。

明确任务示例：

```text
按本项目 Harness 开发：
为供应商管理增加 CSV 导入，支持字段映射、重复 SKU 检测和错误行提示。
按任务风险自动判断 L0/L1/L2，不需要的 Skill、MCP 和 Subagent 不要调用。
```

也可以显式进入 Harness Router（仍保留准备、权限和范围边界）：

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

每个新项目先只读查看接入影响：

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
| 查看当前项目接入影响 | `harness start .` 或 `harness prepare .` |
| 已确认接入范围后准备 | `harness prepare . --apply` |
| 显式初始化 L2 | `harness init --profile L2` |
| 无 Hook 模式 | `harness init --profile L1` |
| 检查当前策略 | `harness defaults` |
| 深度诊断 | `harness doctor . --deep` |
| 完整准备的只读环境预检 | `harness preflight` |
| 更新项目 Overlay | `harness refresh .` |
| Context 状态 | `harness context status .` |
| 保存 Handoff | `harness context handoff . --task TASK_ID` |
| 恢复 Handoff | `harness context recall . --task TASK_ID` |
| 禁用当前项目 | `harness disable` 或 `harness disable .` |
| 组件进程状态/停止 | `harness processes status` / `harness processes stop` |
| 服务状态/启动/停止 | `harness services status` / `start` / `stop` |
| 卸载影响预览 | `harness uninstall` |
| 已确认完整卸载 | `harness uninstall --apply` |
| 保留数据恢复预览/执行 | `harness recover-data` / `harness recover-data --apply` |
| 已卸载版本的记忆服务恢复 | `harness recover-memory` / `harness recover-memory --apply` |

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
→ 检查项目绑定与活动任务
→ 对旧安装根的项目预览并执行 harness migrate
```

新版本接受后，原项目继续绑定旧根，活动或暂停中的任务不会被自动迁移。先完成这些任务，再在任意目录执行：

```bash
harness migrate /你的项目路径 --from /旧Harness安装目录
# 核对版本、项目影响和 Hook 信任要求，确认迁移范围后执行：
harness migrate /你的项目路径 --from /旧Harness安装目录 --apply
```

迁移保留业务文件、任务数据与旧安装目录。输出中的 `transaction_id` 可用于 `harness rollback <ID>`；后续用户修改存在冲突时会停止恢复并保留改动。同安装根更新 Overlay 才使用 `harness refresh`。

不要删除 `~/.codex`，也不要手工覆盖 `accepted.json`。

## 6. 卸载和重装

本轮源码的完整卸载会核对已接受版本、保留版本，以及已登记的候选或准备中安装根。预览中的 `installations` 是本次处理范围；项目代码和任务记录保留，服务数据卷保留。

旧目录缺少依赖所有权清单、存在未登记消费者，或清单与登记记录不一致时，会在清理前报告冲突。不要手工删除状态文件或把未知目录直接标成受管。


在任意目录先查看具体影响：

```bash
harness uninstall
```

它列出受管项目、服务、程序、独立依赖，以及任务记录和记忆的保存位置。运行或暂停中的任务会延后卸载；未知所有权或用户后来改动会停止操作并保留文件。确认这份范围后执行：

```bash
harness uninstall --apply
```

默认停止受管服务、解除项目接入、移除共享入口及已登记的软件和专属依赖。保留业务代码、项目 `.harness` 任务记录、长期记忆数据卷和必要恢复信息。自行维护的 Git/source checkout 保留为用户源码；安装器解包的受管源文件按精确清单清理。结果中 `preserved_data` 给出实际位置。文件恢复、服务恢复和数据库恢复分别报告。

重装使用同一受管安装/状态目录，完整准备会先恢复已保留的数据，再准备当前锁定的软件。也可用 `harness recover-data` 预览，确认后 `--apply`。现有较新数据不会被旧副本覆盖；软件、旧 Hook 信任不会被当作数据恢复。重新接入项目后继续核验原生信任和当前任务证据。

其它已卸载根的记忆卷使用 `recover-memory` 预览和恢复；完整准备入口自动接续。它用当前已准备、与原服务完全一致的镜像重新启动原数据卷，保留地址、Scope 和 Revision。端口占用、缺失或未知数据卷、未知消费者、恢复代次或镜像变化会停止对应恢复，不改项目绑定或自动升级数据库。服务启动和精确记忆回读分别报告；恢复服务也由公共服务入口和完整卸载统一管理。


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
v3.2.0
codex-engineering-harness-3.2.0-linux-x86_64.tar.gz
SHA256：以同一 Release 中的 .sha256 附件为准；一键安装器会自动校验。
```

仓库与 Release 均为 Private。
