# my_claw 0.6.0 实施方案：澄清式交互 + Skills 标准对齐 + 记忆升级

> 目标：本地开发 → 公司/集团 Dify 实测稳定 → 一次性提市场（0.6.0）。
> 原则：内核零 SDK 依赖不破坏；`minimum_dify_version` 保持 null；先测后提。
> 实施节奏：每个 E = 一个晚上能独立完成的量，可乱序但建议按序。

## 总览

| 编号 | 特性 | 一句话 | 新增/改动核心 |
|---|---|---|---|
| F1 | 澄清式交互 | 意图模糊时弹选项让用户选，选完继续原任务 | ask_user 工具 + pending_clarify 状态机 |
| F2 | Skills 标准对齐 | 兼容 agentskills.io 开放标准（Claude/OpenAI/Google 已采纳） | frontparse/packages 校验放宽 |
| F3 | 记忆升级 | 记忆可管理 + 定期整理（做梦） + 情景记忆 | persona/service + 管理命令路由 |

版本：0.6.0（开发期沿用此版本号打本地包，市场未见此版本无需改号）。

---

## F1 澄清式交互（先做，价值最高）

### 行为定义

```
用户：帮我整理一下租赁的资料
AI：  🤔 这个任务有几种理解，你想做哪个？
      1️⃣ 归类整理 —— 按项目分类多份文件，输出清单
      2️⃣ 内容提炼 —— 提取各文件要点，汇总一页摘要
      3️⃣ 生成文档 —— 整理成 Word 汇报材料
      回复数字即可；也可以直接补充说明。
用户：2
AI：  （带着"内容提炼"的理解继续执行原任务，不再重新问）
```

### 状态机

```
正常态 ──LLM调用ask_user──► 澄清态(pending_clarify存KV)
澄清态 ──回复命中选项──► 正常态：query=原任务+「用户澄清：选项N」
澄清态 ──回复未命中───► 仍澄清态(带上下文交给LLM自然处理；连续2轮未命中→丢弃pending)
澄清态 ──超24h────────► 丢弃pending
```

KV 键：`claw:pending_clarify:{conversation_id}`，值：
```json
{"original_query": "...", "options": ["...","..."], "asked_at": 1787000000, "misses": 0}
```

### 代码落点

| 文件 | 改动 |
|---|---|
| `core/tools/registry.py` | 注册 `ask_user`：参数 `question:str, options:list[str]`（2~4项）；handler 写 pending 到 KV，返回 `ClarifyAsked` 信号 |
| `core/kernel.py` | agent 循环收到 `ClarifyAsked` → 终止本轮循环，把问题+编号选项格式化为最终输出（不算错误） |
| `tools/my_claw_tool.py` | ① `_clarify_phase(kv, query)` 加入前置链（onboarding 之后、主循环之前）：命中选项→拼 `original_query + "\n（用户澄清：选择了N - X）"` 后继续主循环；未命中→misses+1（≥2 丢弃），并把 pending 内容注入本轮上下文交给 LLM 自然处理 ② 系统提示词（`core/prompt.py` BASE_PROMPT【工作方式】加一条）：「用户意图存在多种实质不同的合理解读时，调用 ask_user 列 2~4 个选项让用户选，禁止猜；选项要具体到行动差异，不要为细节小事打断用户」 |
| `core/skills/commands.py` 旁 | 新建 `core/clarify.py`：选项匹配器（纯函数，便于单测） |

### 选项匹配规则（`core/clarify.py`，按序尝试）

1. 纯数字/带点带括号：`1` `2.` `②` `3）` → 序号
2. 中文数字：`一/二/三/四`、`第一个/第2个/选3/要3号`
3. 选项文本匹配：回复是某选项的子串（≥4字符）或某选项包含整个回复（≥4字符）
4. 都不中 → 未命中

### 格式化输出（kernel 终轮）

```
🤔 {question}

1️⃣ {options[0]}
2️⃣ {options[1]}

回复数字即可，也可以直接补充说明。
```

### 测试清单（tests/test_clarify.py）

- [ ] 匹配器：`1`/`2.`/`②`/`一`/`第二个`/`选3`/选项原文子串 全部命中正确序号；`帮我改一下`/`算了` 不命中
- [ ] ask_user 工具：写 pending、返回终止信号；options<2 或 >4 拒绝
- [ ] _clarify_phase：命中后 query 拼接正确、pending 删除；未命中 misses 累加；misses≥2 丢弃
- [ ] 24h 过期丢弃
- [ ] 端到端（mock LLM）：模糊任务→ask_user→用户选2→原任务带澄清执行
- [ ] 回归：普通任务不触发 ask_user（prompt 规则 mock 验证不强制，人工测）

---

## F2 Skills 标准对齐（agentskills.io）

### 差异与改动

| 项 | 现状 | 改成 |
|---|---|---|
| 必填 frontmatter | name+description+read-when | **name+description**（read-when 可选，缺省用 description 当触发提示） |
| name 校验 | 无约束 | >64字符或含大写/特殊字符 → **警告+自动转slug目录名**，不拒绝 |
| 未知字段 | 可能报🟡 | 忽略并透传（`license`/`metadata`/`allowed-tools` 等） |
| 目录结构 | 允许 SKILL.md+scripts/ | 同左（标准本就如此，无需改） |

### 代码落点

- `core/skills/frontparse.py`：必填集合从 3 项减为 2 项；未知字段收集进 `extra` 而非报错
- `core/skills/packages.py`：`list_installed` 的 eligible 判定相应放宽（name/description 齐即🟢）；目录名 sanitize
- `tools/skill_manager.py` 帮助文案：注明「兼容 Claude/OpenAI Agent Skills 标准格式（agentskills.io）与 OpenClaw 目录结构」

### 测试清单（tests/test_skills_std.py）

- [ ] 仅 name+description 的 SKILL.md → 🟢可用
- [ ] 含 `license`/`metadata`/`allowed-tools` 未知字段 → 不报错且透传
- [ ] name=`Report Generator`（大写空格）→ 警告 + 目录转 `report-generator`
- [ ] Fixture：从 agentskills.io 生态抓 2 个真实 SKILL.md 样本存 `tests/fixtures/skills_std/`，安装解析全过（离线 fixture，不依赖网络）

---

## F3 记忆系统升级

### 3a 记忆管理命令（确定性路由，仿 commands.py）

命令（在 `_onboarding_phase` 之后、主循环之前路由）：
- `查看记忆` → MEMORY.md 条目编号列出（附条目类型标签）
- `删除记忆N` / `忘记记忆N` → 删除第 N 条并回显删了什么
- `修改记忆N为...` / `记忆N改成...` → 更新第 N 条并回显前后对比

路由规则（防误伤）：必须同时含「记忆」+ 明确动词（查看/列出/删除/忘记/修改/改成），疑问句不算（`你记得我生日吗` 含"记"不含动词+记忆组合，不路由）。写进 `core/memory/commands.py` 纯函数 + 单测。

### 3b 记忆整理（"做梦"）

- 触发：每日 digest（`core/memory/service.py` 已有）跑完后，若 MEMORY.md 条目 > 30 → 追加一次整理调用
- 整理 LLM 任务：合并重复、解决矛盾（保新弃旧）、`[经历]` 类超 60 天未更新移入 `MEMORY.archive.md`（不删，可翻）
- 护栏：整理前把原文存 `MEMORY.bak.md`（保留一代，可回滚）；整理后条目上限 40；整理只动 MEMORY.md，绝不碰 USER.md/IDENTITY.md/SOUL.md
- 失败策略：LLM 输出解析失败 → 放弃本次整理（bak 不落盘），日志告警

### 3c 情景记忆

- 条目格式：MEMORY.md 每行加类型前缀 `[事实]` `[偏好]` `[经历]`（解析器对无前缀旧条目宽容，默认 `[事实]`）
- 捕获时机：**只在每日 digest 里提取**（不加每任务一次的 LLM 调用，省 token）——digest 提示词增加：「提取关于用户的新事实/偏好/值得记住的经历（如他处理某类任务的做法、上次某场景的决策）」
- 使用：`persona.build_context()` 已注入 MEMORY.md，情景自然带上；`[经历]` 条目写入时在行尾加 `（来自M月d日对话）` 便于追溯

### 测试清单（tests/test_memory_upgrade.py）

- [ ] 命令路由：5 种管理命令命中；`你记得…吗`/`记住这个词` 不误路由
- [ ] 查看/删除/修改 round-trip（真 KV mock）
- [ ] 整理：>30 条触发；合并/矛盾/归档各一例；解析失败回滚（MEMORY.md 不变）
- [ ] bak 文件：整理前生成、二次整理覆盖前一代
- [ ] digest 提取：mock 对话 → 产出三类条目且带前缀和日期

---

## 实施顺序（每晚一个 E，可独立提交）

| 晚 | 内容 | 完成标志 |
|---|---|---|
| E1 | F2 标准对齐 + tests/test_skills_std.py | 全绿，agentskills.io fixture 装得上 |
| E2 | F1 core：clarify.py + ask_user 工具 + kernel 终止 + 单测 | 单测全绿 |
| E3 | F1 接线：_clarify_phase + prompt 规则；打本地包装进公司 Dify 实测 | 公司Dify 实机：模糊任务→选项→选择→继续 全链路 |
| E4 | F3a 管理命令 + 单测 + 实测 | 公司Dify 实机：查看/删除/修改记忆 |
| E5 | F3b+3c 整理与情景记忆 + 单测 | 单测全绿 + 手工触发一次 digest 看产出 |
| E6 | 全量回归（60+ 新旧测试）→ 打 0.6.0 包 → 公司 Dify 装测 → 集团 Dify（my_claw 演示应用 + 租赁伙伴）各跑一轮三特性 | 四应用全绿 |
| E7 | CHANGELOG/README/中文README 更新 → 市场提交流程 | PR 提交 |

## 实测脚本（公司 Dify，装本地包）

```bash
cd f:/workspaces2/test/my_claw
python scripts/package.py          # dist/my_claw.difypkg（0.6.0）
# 公司Dify装包（同ID升级daemon不刷新的坑已知）：卸载再装，或SQL改三表版本指向
```

实机验证话术：
- F1：`帮我整理一下租赁的资料`（应出选项）→ 回复 `2` → 验证按选项执行
- F1 防骚扰：`现在几点了`（不应出选项）
- F3a：`查看记忆` → `删除记忆1` → `查看记忆`
- F3b/c：连续对话两天后看 digest 产出与整理行为
- F2：上传一个 agentskills.io 格式的 zip → `新增技能` → `查看技能` 应🟢

## 市场提交清单（0.6.0 时照抄）

1. PR 单文件（仅 .difypkg）——README/图全在包内
2. 勾选行精确 `- [x] High risk`，不加任何备注
3. 包内无 `__pycache__`/`.pyc`/`_*.txt`（package.py 已防，重打包前 `unzip -l | grep -E "pycache|_fids"` 自查）
4. 主 README.md 零 CJK（新特性描述先写英文，中文进 readme/README_zh_Hans.md）
5. 提交前在服务器跑全套 marketplace-toolkit validator 且 **cat 错误文件**（勿 json 解析）
6. Git Data API 从公司服务器提交；PR 正文复用 `_myclaw_pr_workspace/_pr_body_055.md` 改版本号

## 风险与回滚

| 风险 | 对策 |
|---|---|
| ask_user 过度触发打断体验 | prompt 限定"实质不同的解读"；实测话术防骚扰项；0.6.1 可加开关参数 `clarify_enabled` |
| 记忆整理误删 | MEMORY.bak.md 一代回滚 + 归档不删除 + 只动 MEMORY.md |
| 标准对齐后老技能包行为变 | 兼容为超集：老格式（含 read-when）继续工作；回归测试含旧 fixture |
| 公司Dify升级坑 | 沿用已知流程：卸载重装或 SQL 三表改版本；技能包卸载会丢→先 `下载技能N` 备份 |

## 不做（本轮明确出界）

- 不做向量库/嵌入检索（Letta 基准显示精心维护的文件记忆已够，保持零重依赖）
- 不做真正的 UI 按钮（Dify 插件无此能力，编号选项+建议按钮已够用）
- 不做每任务一次的情景提取调用（token 成本，全走每日 digest）
