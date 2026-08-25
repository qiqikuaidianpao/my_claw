# Changelog

## 0.6.1（2026-08-25，编排开关与国际化命令）

- **clarify_enabled 编排开关**：工具节点参数（默认开）。关闭后 ask_user 从模型工具列表隐藏、提示词切换为"按最可能解读直接执行"，并清理历史挂起的澄清状态——编排者可按场景彻底关停澄清交互
- **记忆管理命令英文别名**：`view memory` / `delete memory N` / `edit memory N to …` / `view archived memory`（含 forget/remove/update/change、my memories、entry no. 等变体），回复语言跟随命令语言（中英双语模板）
- 工具 schema 增加 clarify_enabled 参数（中/英/葡/日四语说明）

## 0.6.0（2026-08-24，市场发布版：澄清交互 + agentskills 标准对齐 + 记忆升级）

- **澄清交互（ask_user）**：模糊任务不再瞎猜——agent 可在执行中途弹出 2–4 个带 emoji 序号的选项（1️⃣2️⃣3️⃣4️⃣），回复数字即按所选解读继续；防骚扰护栏：仅"实质不同的解读"才问、无关回复两次自动丢弃、24 小时过期；纯确定性匹配不耗额外 LLM 调用
- **技能包对齐 agentskills.io 开放标准**：SKILL.md 只需 `name` + `description` 即可通过校验（Claude / OpenAI 等均在采用）；兼容旧格式（`read-when` 继续生效并作为触发提示）；目录名自动规范化（小写-连字符、≤64 字符，含中文/空格自动转换）
- **记忆管理命令**：`查看记忆` / `删除记忆N` / `修改记忆N为…` / `查看归档记忆` 确定性路由（零 LLM 开销），编号列出、精确增删改、带越界保护；疑问句（如"你记得我生日吗"）不误路由
- **睡眠整理（做梦）**：每天首轮自动提取昨日对话为三类记忆——`[事实]` `[偏好]` `[经历]`（经历带日期来源）；条目超 30 条触发整理：合并重复、矛盾保新、超 60 天未更新的经历归档到 MEMORY.archive.md（不删除、可翻看）；整理前原文备份 MEMORY.bak.md（一代可回滚），LLM 输出解析失败自动放弃不落盘
- **首用引导防误吞**：称呼必须"像称呼"（2–12 字、无标点、非任务动词/问句开头）；用户回复任务时放行正常处理而非误存为称呼；重置人格后单轮即可重新落档；引导状态按 应用+访客 隔离，多应用/多访客不再串台
- 单测增至 115 项全绿；公司 Dify（1.16.1）与集团 Dify 双环境四应用实测通过（澄清/记忆/技能/引导全链路）

## 0.5.5（2026-08-20，市场发布版：品牌与文案统一）

- 首用引导改为两段式短流程（问称呼 → 落档开工），移除多字段问卷；用户可见文案全面统一为 my_claw 品牌，不再出现 Mini Claw 字样
- 市场 简介/README 重写为通用口吻（对齐 mini_claw 的"小龙虾"叙事），出处说明移至文末 Provenance 节
- provider / tools 描述同步更新；无功能行为变更，单测全绿

## 0.5.4（2026-08-17，技能列表展示优化）

- 「查看技能」输出增加技能介绍：`list_installed` 透出 `description`，列表渲染在技能名/版本/状态下一行展示介绍，并汇总"共 N 个、M 个可用"与操作提示
- 五个官方技能包（office / rental-calc / chart / schedule / feishu-msg）的 SKILL.md 描述本地化为中文在前、英文括注的双语格式

## 0.5.3（2026-08-16，技能管理路由修复）

- 收紧 `my_claw` 的技能管理前置路由：仅查看、新增、删除、下载、依赖安装等明确管理意图进入 `skill_manager`
- 普通任务即使包含“技能”字样也继续进入主 agent 循环，修复群通知文案“技能热插拔”被误吞的问题
- 技能管理自然语言解析收口到单一实现，并支持“帮我看看技能列表”等语序变化
- `skill_manager` 补齐依赖安装动作及工具参数说明
- 部署约束：Chatflow 所有输入统一接入 `my_claw`，移除“包含技能”外部分支，避免绕过唯一管理路由
- 测试增至 64 项，另通过真实 Dify SDK 插件注册与子进程启动校验
- Marketplace 合规：manifest 补充必填 `repo` / `contact` 字段；`dify_plugin` 依赖提升至 `>=0.9.0`（官方市场最低版本要求）

## 0.5.0（2026-08-14，特色功能补齐）

补齐原 mini_claw 的三项标志性能力，赶在 marketplace 审核合并前：

- **人格引导（onboarding）**：首用问称呼→写入 USER.md/IDENTITY.md；"重置人格"清空重来。两段式状态机，可跳过（skip_onboarding 参数）
- **执行审批（exec approval）**：敏感命令（curl/pip/npm/git 等白名单外）挂起等待用户裁决——回复 1=本次允许 / 2=总是允许（按命令名持久授权）/ 3=拒绝；挂起状态跨调用保存在插件存储
- **新增三个内置工具**：
  - `web_fetch`：SSRF 防护的网页抓取（模型可直接联网查资料）
  - `update_persona`：模型自主沉淀长期记忆/用户画像到四文档人格
  - `run_skill_command`：在技能目录内规范执行脚本（解释器统一解析）+ 产物自动收割到工作区 skill_outputs/（替代此前"复制脚本到工作区"的绕行）
- 测试增至 59 项（新增 onboarding/审批状态机/新工具用例）

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
