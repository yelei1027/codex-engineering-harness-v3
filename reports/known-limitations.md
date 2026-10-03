# 已知限制与人工入口

- 新项目默认使用 L2 安装 profile；任务风险仍独立按 L0/L1/L2 分类。当前 L2 验收项目的 10 个项目 Hook 已由用户通过 `/hooks` 审阅并信任；Codex 0.160.0 原生 lifecycle smoke 已实际观察 SessionStart、UserPromptSubmit、Stop、PreCompact 和 PostCompact，恢复核验返回 RESUME_OK。多个同事件 Handler 不假设执行顺序。
- Codex Desktop bundled runtime `/usr/lib/chatgpt/resources/codex` 当前为 0.160.0，可用于 app-server、MCP、Skills/Hooks inventory，但不是完整 standalone TUI。Desktop 模式下 `harness start` 返回 `DESKTOP_PROJECT_READY` 和准确 repo；用户需在 Desktop 将该 repo 作为主文件夹并新建会话。完整 standalone CLI 才允许直接 exec。
- 4 个项目 MCP 已在 Codex 0.160.0 上重新做当前 runtime 实际调用：context-mode/`ctx_execute`、MarkItDown/`convert_to_markdown`、PowerContext/`list_scopes`、CodeGraph/`codegraph_explore`，全部 PASS。对应证据见 `reports/final-mcp-runtime-smoke-3.1.0.json`。
- PowerContext 当前正式使用 Basic explicit Handoff；真实 Scope、Revision 与独立客户端 Recall 已有专项 smoke。Generation、Embedding 与 Full 自动 Memory 未配置，保持可选能力，不阻塞核心 Harness。
- screenshot-to-code sidecar 的本地前后端/预览健康可用，但第三方模型 Provider 未配置；真实 Provider 生码仍为 OPTIONAL_PROVIDER_BLOCK。默认截图路线优先使用当前 Codex Desktop 原生图像理解。
- 项目 Overlay 是默认能力来源：repo 的 `.agents/skills`、`.codex/config.toml`、`.codex/hooks.json` 只在该 repo 作为当前受信任主项目时加载。Settings 的全局 MCP/Skill 页面不能替代项目级 runtime inventory。全局安装同一批 Harness Skills 默认不启用，以避免重复注册和 Skill 污染。
- 当前核心回归为 66 passed + 4 subtests。V3.1.1 新增项目路径解析回归：`harness disable` 可从已启用项目任意子目录解析项目根；父级集合目录不会误选嵌套项目。候选类别故障注入使用隔离测试进程，不等于所有第三方未来版本升级、网络下载失败、数据库迁移都已在本机演练。
- Workflow JSON 是 107 节点设计契约。结构、边、恢复白名单和本地状态机测试与自然语言样例/远端发布实测分开；未执行的 CI、合并或生产部署不标 PASS。
- CLI doctor 只把它真实执行的项目标为 PASS；专项 MCP、Hook lifecycle、Subagent、Review 和 Recovery 证据由独立报告关联，不能用静态配置冒充功能通过。
- 发行包是在线可复现安装包，不是完整离线包；依赖重建仍可能需要网络。完整新主机 Docker 镜像构建也依赖可用 Docker/网络环境。
- uninstall 只移除受管控制平面和 launcher；源码、隔离依赖、备份及服务数据保留。停止 sidecar 使用 `python3 scripts/services.py stop`，不会删除 named volumes。
- GitHub 远端发布只允许在最终 secret audit、release commit 和私有仓库可见性核验后完成；不得把 machine binding、凭证、数据库、task state、聊天记录或业务数据推送到仓库。
