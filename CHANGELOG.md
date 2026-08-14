# Changelog

## 0.4.0（2026-08-14，发布候选）

首个完整功能版。基于 mini_claw v1.2.0（commit ccbe15b，Apache-2.0，经原作者授权）的架构级重构。

### 架构

- **平台无关内核 `core/`**：零 SDK 依赖，52 项单元测试全绿
  - `kernel.AgentKernel`：四阶段状态机（生成器化，支持宿主实时排水），取代原 2258 行单方法主循环
  - `session.SessionContext` / `LoopGuard`：单一状态对象 + 循环熔断
  - `llm.collect_round`：整轮缓冲 + **推理双通道**（优先 `reasoning_content` 独立字段，回退 `<think>` 标签解析与配对兜底）
  - `tools.registry`：装饰器注册工具分发（进度文案参数化）
  - `skills.packages`：pydantic 强类型技能清单 + stdlib frontmatter 子集解析器（零 PyYAML 依赖，插件运行时约束）
  - `memory`：PersonaStore（四文档人格模型 + 托管记忆块合并去重）+ MemoryService（每日摘要/跨会话召回/GC）
  - `context.ContextManager`：token 预算压缩（LLM 摘要 + 记忆提取）+ 会话历史持久化
  - `workspace.Workspace`：显式产物注册与交付、会话目录轮转
  - `errors` / `log`：类型化异常层次 + 结构化脱敏日志（chunk 级诊断）
- **Dify 适配层 `adapters/dify/`**：LLMClient（流式整轮 + 历史重建规范化）/ MessageEmitter / KVStorage
- **继承的安全资产**：命令执行安全闸（python/python3 系列统一映射运行时解释器）、SSRF 防护、zip-slip 防护、路径逃逸防护
- **参数化技能管理**（skill_manager）：list/install/remove/download，取代中文正则命令解析

### 实测验证（公司 Dify 1.11.1 + glm-5.2）

| 场景 | 结果 |
|------|------|
| 5 技能包热插拔安装 | ✅ 全部 🟢可用 |
| 渐进式披露（读 SKILL.md → 执行 → 交付） | ✅ 全程进度可见 |
| 租金测算 → Excel 交付 | ✅ 月供 152,109.69 元（与集团 Dify 上 mini_claw 结果一致） |
| 合同生成 → Word 交付 | ✅ 含自检与要点汇报 |
| 润工作群通知（webhook） | ✅ 端到端送达（StatusCode:0） |
| 跨会话记忆 | ✅ 全新调用正确回忆先前会话信息 |
| 多轮工具调用 / 错误可读 | ✅ |

### 已知限制

- 老版本 Dify（1.11）守护进程对"同插件 ID 升级"的记账在特定状态下不生效；全新安装路径无问题（开发期以换名迭代规避，详见排查文档）
- `金控-Qwen3.5-35B-A3B` 网关模型不产生 function calling（口头扮演工具）；glm-5.2 实测正常
- workflow（无会话）形态下不回放会话历史（防提示词膨胀）；附件下载建议在 Chatflow 形态使用
- 技能脚本在技能目录内不可直接执行（安全策略）；智能体会复制到工作区执行——后续版本将提供标准 run_skill_script 工具

## 0.1.x（2026-08-14，开发迭代）

M0-M3 里程碑：内核落地、适配层、技能/记忆/上下文管理、多次守护进程兼容性修复（tags 枚举 / description 结构 / human_description / select 参数 / 跨模块导入 / PyYAML 依赖 / ToolCall 规范化）。
