# Codex Engineering Harness

给 Codex Desktop 使用的项目开发流程：先理解需求、确认范围，再实现、验证和交付。

当前发行版：[v3.3.0](https://github.com/yelei1027/codex-engineering-harness-v3/releases/tag/v3.3.0)。支持与验证环境为 **Linux x86_64 + Bash + Codex Desktop**。本说明不提供 Windows PowerShell/CMD 安装方案；WSL 也未作为独立支持环境完成验收。

按需服务、空闲回收及第三方模型配置见 [v3.3 资源管理说明](docs/RESOURCE-MANAGEMENT-3.3.md)。原生 Desktop 未验证项保留为已知限制，见发行说明。

## 先分清三个操作位置

| 标记 | 在哪里操作 | 放什么内容 |
| --- | --- | --- |
| **终端 · Bash** | 安装 Harness 的那台 Linux 电脑，打开系统“终端”；用日常登录用户 | `gh …`、`bash …`、`harness …`、`python3 …` |
| **客户端 · 聊天框** | Codex Desktop 打开你的业务项目后，在消息输入框发送 | “用 Harness……”等自然语言；界面支持时可选择 `$harness-dev` |
| **客户端 · 界面操作** | Codex Desktop 的项目、权限菜单和 Hook 信任提示 | 打开文件夹、选择权限、审阅信任；这里没有 Bash 命令 |

把命令发给 Agent 是委托它执行，不等于你已在终端执行成功。终端中的 `.` 表示当前目录；本指南优先使用绝对路径。`/你的项目路径` 等占位符必须替换，不要原样复制。不要把安装目录当成业务项目目录。

## 1. 首次安装：只在尚未安装 Harness 的机器执行

**位置：终端 · Bash。工作目录：任意目录。前提：Linux x86_64、Git、Python 3、Node.js/npm、uv、Docker Compose v2、用户级 systemd、Codex Desktop 和 GitHub CLI 已准备。**

仓库已公开，浏览源码和下载发行文件无需仓库访问授权。当前安装器使用 GitHub CLI，并检查其登录状态；按现有安装流程先登录你的 GitHub 账号：

```bash
gh auth login
gh auth status
```

预期：第二条命令显示 GitHub CLI 登录成功。然后下载安装脚本到独立临时目录并运行指定版本：

**位置：同一个 Linux Bash 终端。用途：首次安装 v3.3.0。**

```bash
harness_download_dir="$(mktemp -d)"
gh release download v3.3.0 --repo yelei1027/codex-engineering-harness-v3 --pattern install-release.sh --dir "$harness_download_dir"
bash "$harness_download_dir/install-release.sh" v3.3.0
```

预期：下载发行包、核对 SHA256、准备锁定组件、准备受管服务、完成发现后休眠并登记 `harness`。默认安装到 `~/.local/share/codex-engineering-harness/releases/3.3.0`。**安装组件不等于项目已接入，也不等于 Hook 已信任。**

**位置：终端 · Bash，任意目录。用途：让当前终端找到入口，并检查安装。**

```bash
export PATH="$HOME/.local/bin:$PATH"
harness --help
harness status
```

`export` 只影响当前终端。新终端仍找不到命令时，可用 `~/.local/bin/harness` 代替 `harness`。若输出“已有可用 Harness”，不要重复安装，转到第 5 节更新流程。

## 2. 第一次接入一个业务项目

**位置：终端 · Bash，任意目录。前提：Harness 本体已安装。把路径替换为你的业务项目根目录。**

```bash
harness start "/你的项目路径"
```

预期：新项目返回 `PREPARATION_REQUIRED` 并列出接入影响；已就绪项目应跳过重复准备。先读清楚是否初始化 Git、写入哪些配置、技能和 Hook。

**位置：终端 · Bash，任意目录。仅在你确认上面的接入范围后执行：**

```bash
harness prepare "/你的项目路径" --apply
```

预期：完成项目接入，保留业务文件和无关配置，不创建 Git 提交。默认 L2 接入包含 Hook；不需要 Hook 时，在这条准备命令后加 `--profile L1`。这是接入能力配置，不是本次任务的风险等级。

## 3. 在 Codex Desktop 中开始工作

**位置：客户端 · 界面操作。**

1. 将上述业务项目目录作为主文件夹打开，新建聊天。
2. 审阅项目配置及 Hook 信任提示。若客户端支持 `/hooks`，在聊天框调用它查看；若没有该入口，以实际原生信任提示为准，不要到终端运行 `/hooks`。
3. 在消息框下方的权限菜单选择项目提供的 `harness-inspect`，然后让 Agent 核对本轮实际权限。菜单没有该项时先诊断项目加载，不把“请求批准”当作只读。

**位置：客户端 · 聊天框。复制这段话发送，不是在 Bash 或 PowerShell 中执行：**

```text
用 Harness，优化一下这个项目。先只读检查，给我少量有证据的建议，再引导我确认具体范围。
```

预期：Agent 先检查并提出关键选择；未确认业务修改范围前不改代码。同一聊天后续无需重复“用 Harness”，换项目或新聊天需重新明确选择。

**位置：客户端 · 聊天框与权限菜单。** 确认具体目标、允许修改的文件和验证方法后，再在原生菜单开放当前项目写权限。Agent 应核对实际权限后自主完成范围内的工作。交付或取消后恢复 `harness-inspect` 并核验。项目准备批准不等于业务修改批准，MCP 外部副作用也须单独核对。

## 4. 日常操作速查

下表中的 `harness` 命令全部在 **Linux 终端 · Bash** 执行；使用绝对项目路径时可在任意目录运行。

| 目的 | 命令 | 预期 |
| --- | --- | --- |
| 安装状态 | `harness status` | 显示当前登记与版本 |
| 环境预检 | `harness preflight` | 区分通过、缺失和权限下未核验 |
| 项目诊断 | `harness doctor "/项目路径" --deep` | 分项报告，未验证不算通过 |
| 服务状态 | `harness services status` | 列出受管服务 |
| 停止服务以释放资源 | `harness services stop` | 停止受管服务，保留数据；使用前需启动 |
| 重新启动服务 | `harness services start` | 启动受管服务 |
| 组件进程状态 | `harness processes status` | 查看受管组件进程 |
| 卸载预览 | `harness uninstall` | 展示资源范围和保留数据位置 |
| 确认后卸载 | `harness uninstall --apply` | 按受管范围清理，保留业务与记忆数据 |

**位置：客户端 · 聊天框。** 日常需求、解释代码、选择优化方向、请求保存 Checkpoint/Handoff 都在这里发送。例如：“保存当前任务的本地 Checkpoint，核验后再压缩上下文”。`/compact` 是客户端入口，只有界面提供时才使用，不能在 Bash 中执行。

## 5. 已安装旧版：不要重跑首次安装命令

更新包括下载来源、准备隔离候选、实际验收、接受版本、迁移旧项目。不是单独执行 `refresh` 就完成升级，也没有一条可省略验收的通用 `promote` 命令。

**推荐位置：客户端 · 聊天框。让 Agent 根据你的实际安装路径操作：**

```text
请把已安装的 Harness 更新到 v3.3.0。先只读核对当前版本、安装根和项目绑定，展示候选更新与项目迁移范围；确认后再执行。保留任务、记忆和无关配置，不直接覆盖旧安装。
```

手动维护者在 **Linux 终端 · Bash** 先运行 `harness update --help`，再按 [详细维护说明](INSTALLATION.md#更新与项目迁移) 操作。`--to` 必须指向真实的新版本来源目录；`--acceptance-plan` 需要实际验收命令，不能填造假的通过结果。已有任务结束后才能按适用范围迁移项目。

## 6. 下载与深入说明

- [v3.3.0 下载页](https://github.com/yelei1027/codex-engineering-harness-v3/releases/tag/v3.3.0)：下载 `codex-engineering-harness-3.3.0-linux-x86_64.tar.gz` 及同名 `.sha256`；GitHub 自动生成的 Source code 压缩包不是完整发行安装包。
- [详细安装、更新、恢复说明](INSTALLATION.md)：维护命令、前提与边界。
- [发行说明与已知限制](docs/RELEASE-3.3.0.md)：实际验证范围及尚未验证项。
- [完整流程 HTML](docs/codex_engineering_harness_v3_parallel_flow.html)：下载后在浏览器打开；它是说明书，不是命令执行器。

当前发行不自动替换你的本机安装。原生补丁拒写原始返回不可获取等限制仍保留；正式发布不代表完整 PRD 的所有原生验收已通过。

## 许可证与源码贡献

Harness 自有实现采用 [MIT License](LICENSE)。集成组件保留各自许可证，详见 [THIRD_PARTY_NOTICES](THIRD_PARTY_NOTICES)；其中 context-mode 使用 Elastic License 2.0，其使用与再分发限制以该许可证为准，不能将整个集成包都视为 MIT。

欢迎通过 Issues 提交复现步骤或需求，通过 Pull Requests 提交改进。修改前阅读 [AGENTS.md](AGENTS.md)，保留用户配置与数据，说明验证范围；未经验证的原生 Desktop 或 Provider 行为应标明未验证。

源码中的 [完整流程图](docs/codex_engineering_harness_v3_parallel_flow.html) 保留 V3 蓝白布局，并由当前工程契约生成节点内容。发行版附件对应发布时的快照，最新文档以仓库默认分支为准。
