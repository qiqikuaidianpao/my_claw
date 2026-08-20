# my_claw

**作者：** qiqikuaidianpao
**版本：** 0.5.5
**类型：** Tool（工具插件）

### 概述

my_claw 是一个基于 Dify 平台的轻量化"小龙虾"——一个有"灵魂"的 AI 伙伴。它具备短期、长期记忆，具备身份、性格与灵魂设定，通过它可以让你感受到 AI 的温度：用得越多，它越懂你。快来领取属于你和你企业的专属 AI 助手吧~

my_claw 采用 **Skill 渐进式披露（Progressive Disclosure）** 执行模式：把技能包当作"工具箱"，让 Agent 在任务命中时才逐步读取技能说明，再按需读取文件、执行脚本，最终生成文本或文件交付。技能可热插拔——在对话里就能安装和卸载。

### 适用场景

- 想要一个记得住你、越用越懂你的"有灵魂"AI 助手
- 想为自己或团队定制一个有身份、有性格的专属助手
- 想通过投喂技能包持续扩展它的能力（文档、图表、日程、群消息，以及你自己的）

### 工具

本插件共有两个工具：

- **my_claw**：有灵魂的 AI 助手，用于对话和任务执行。具备短期、长期记忆和身份、性格、灵魂设定，根据用户输入提供个性化服务。
- **技能管理**：管理技能目录。支持查看/安装/删除/导出技能，以及依赖检测与依赖安装。

  ![tools](../_assets/shot-tools.png)

### 使用方法（Dify 中）

1. 从插件市场安装本插件（或通过本地 `.difypkg` 文件安装）。
2. 自部署用户：请在 Dify 的 `.env` 中把 `FILES_URL` 设置为你的 Dify 地址（改完重启 Dify），否则可能无法获取上传的文件。
3. 按下图编排工作流——消息里包含"技能"的走**技能管理**，其余走 **my_claw**：

   ![workflow](../_assets/shot-workflow.png)

4. 与 my_claw 对话、设置人格——说一次你的称呼和偏好，它就跨会话记住你：

   ![persona](../_assets/shot-persona.png)

   提示：发送 `重置人格` 可清空身份与记忆，重新开始。

5. 用技能管理扩展能力——上传技能包（.zip）并说"新增技能"。技能支持查看/新增/删除/导出、可用性检查和依赖检测安装：

   ![skill](../_assets/shot-skill.png)

   技能包是一个目录（zip 打包），内含 `SKILL.md` front-matter 清单：

   ```yaml
   ---
   name: my-skill
   description: 这项技能做什么
   read-when: 什么时候应该加载这项技能
   metadata:
     requires:
       bins: [python3]
   ---
   # 给 Agent 的说明书……
   ```

   特性一：内置依赖检测与安装。Agent 不再允许自行安装依赖——请在 `SKILL.md` 的 `metadata` 里声明依赖而不是写进正文，运行 `依赖安装` 可自动安装可装部分。

   特性二：兼容 OpenClaw 的技能目录结构——带 YAML front-matter 元数据的标准技能开箱即用。

### 常见问题

- **某些模型没有可见回复**：供应商插件需支持函数调用/工具调用；换模型或升级供应商插件通常即可解决。
- **技能未被调用**：技能包越完整、调用越顺——请确认文件与脚本符合上述标准格式。

### 作者与联系方式

- 仓库：<https://github.com/qiqikuaidianpao/my_claw>
- 联系：在上述仓库提 issue。

### 开发

```bash
pip install -r requirements.txt pytest
pytest tests/ -q           # 内核单元测试（无需 Dify）
python scripts/package.py  # 打包 my_claw.difypkg
```

智能体内核平台无关（零 SDK 依赖、单元测试全覆盖），Dify 只是一个适配层。

### 出处与许可证

Apache-2.0。my_claw 是 [mini_claw](https://github.com/lfenghx/mini_claw) v1.2.0（作者 lfenghx）的现代化重构版，经原作者授权二次开发发布；逐模块出处与致谢记录于 [NOTICE](../NOTICE)。
