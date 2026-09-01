# AI 赛后反馈这条链路还能怎么提升

> 起点是 2026-08-25 的问题：**目前这套软件的 AI 赛后反馈功能还能有什么提升？**
> 附带一个待评估的想法：引入 IBM Granite TSPulse R1（遥测异常检测 / 缺失值处理 /
> 相似片段检索）和 Granite Embedding 97M Multilingual R2（跨场次检索与中英文建议）。
>
> 第 1 节是必须先知道的一件事，它决定后面所有取舍。第 2 节是按性价比排的清单。
> 第 3 节是对那两个模型的判断。第 4 节是建议顺序。每一条都指向具体代码位置。
>
> 姊妹文档：[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md)
> 讲的是"能不能证明有提升"，这一篇讲的是"给参与者看的那份反馈本身够不够好"。

---

## 1. 现状：模型其实一个字都没写到参与者眼前

这不是修辞。`src/f1coach_core/llm.py:254` 在任何校验之前无条件调用
`_ground_model_prose`，而它做的事是（`llm.py:439-440`）：

```python
finding["focus"] = EVIDENCE_METRICS[selected["metric"]][3]
finding["issue"], finding["cause"], finding["action"] = guidance[selected["metric"]]
```

`guidance` 是 15 条写死的字符串（`llm.py:340-437`），键恰好覆盖
`opportunity_catalog` 能产出的全部 metric —— 也就是说这个覆盖是**必然发生**的，
不是兜底路径。

再加上另外两道闸：

| 闸 | 位置 | 效果 |
|---|---|---|
| JSON Schema 把每条 citation 做成 `oneOf[{const: 真实值}]` | `llm.py:build_coach_response_format` | 数字模型根本没机会自己写 |
| prose 里出现任何数字直接判失败 | `coach.py:_require_measurement_free_prose` | 措辞里不许带测量值 |
| 散文被模板覆盖 | `llm.py:_ground_model_prose` | 模型写了也不算 |

**所以 Granite 4.1 在主面板上的全部自由度是：从候选目录里挑哪几个
`(弯, 指标)`、挑几条。** 零幻觉是真的，代价是天花板被钉死在 15 条模板上。

唯一真让模型写句子的是另一条路 —— `racecoach/granite/narrate.py`：它给模型一个
数字白名单，越界的句子整句丢掉（`narrate.py:_accept`）。但它的输出只喂给 review
窗口和 replay（`analysis_view.py:_apply_narration`），**主面板看不到**。

### 1.1 这意味着两个不同的方向

- **把确定性层做厚** —— 安全、便宜、可控、可写进论文。下面清单里绝大多数价值在这一侧。
- **把模型放出来一点** —— 有上限，但需要一层新的校验（不是"数字白名单"那么简单，
  而是"允许的断言类型"白名单），而且要把**模板回退率**记下来当质量指标。

两者不冲突：正确做法是让模型写、写砸了回退到模板，而不是像现在这样永远用模板。

### 1.2 一个应该量化的问题（**已量化，见 §9**）

既然散文是模板、数字是 `const`，那这套 LLM 调用相对 `MockCoach`
（`coach.py:MockCoach`，它同样从真实 evidence 里挑最差的弯）到底多带来了什么？

对同一批 session 跑两边、比 finding 重合度就知道。**如果重合度很高，"AI 辅导"这个
干预在论文里的措辞就要小心。** 这不是坏消息，是必须先知道的事。

> **已量化（2026-08-28），见 §9。** 重合度低：首条引用重合 0.12，44 份审计里只有
> 1 份是完全一样的三张卡。所以这个干预**不是** `MockCoach` 换层皮。但差异是
> 系统性的（mock 83 条里 62 条说 `min_speed`），**只能说选择不同，不能说模型更准**。

---

## 2. 提升清单（按性价比排）

### A. 会话级 debrief 根本没接到 GUI —— 最大的一个洞

`racecoach/granite/report.py` 的 `build_report` + `render_markdown` 已经能产出
**整段 session** 的 debrief，`racecoach debrief <session_dir>` 能跑（`cli.py:436`）。

但 GUI 里只有 per-lap 的 `Export Analysis Report…`（`main_window.py:254`），参与者
读到的是三张互不相干的单圈卡片。

而研究要的干预本来就是"跑完 baseline 三圈之后读到的那一份东西"——那应该是一份
**会话级**的东西，不是三张卡。**代码写好了，只是没有入口。**

> 成本：半天。**已完成，见 §5。**

### B. 跨弯角的模式没人说

`coach.py:coachable_corners` 按 `time_lost_s` 排序、逐弯独立出卡
（`MAX_COACHING_CORNERS = 6`，`MAX_FINDINGS = 3`）。一个每个弯都刹早的人拿到三张
几乎一样的卡，而教练价值最高的那句"你**普遍**刹得早"没人说。

这句话是确定性可算的：对 `_corner_facts` 的 `brake_point_m - ref_brake_point_m`
取符号一致性 + 中位数，跨弯角聚合。数字仍然确定 → 仍然零幻觉；模型只负责措辞。

> 成本：一天。**已完成，见 §8。** 落地时没有走 contract 那条路（理由见 §8.5），
> 也不是对称报告的（真实数据推翻了，见 §8.4）。

### C. 没有"上一条建议有没有落地"

> **这一条原先的写法是错的，2026-08-27 改正。** 原文说的是"参与者永远不会被告知
> '上一圈说你 T3 刹早 12 m，这一圈是 4 m'"，并把它当成给参与者的反馈来提。但本研究
> 的流程是 baseline 三圈 → 读 debrief → 第二段三圈（`capture_pages.py:62-65`），
> **圈与圈之间从来没有给过任何建议**。同一段里 lap N 到 lap N−1 的变化是对赛道的
> 熟悉，不是依从；把它当成"你改过来了"给参与者看是错误归因。而真正的依从度到达时
> 已经没有下一段可开了，所以**它对参与者本次的驾驶提升为零**。

价值全在测量侧，而且不小。`exposure.py` 已经把"剂量"量得很细（看了多久、看了几个弯、
其中多少秒屏幕上有 AI 建议），结果端量的是圈速，**中间缺一环**：如果 coached 组没比
control 组快，现在没有任何办法区分"建议本身没用"和"建议没问题但没人照做"——论文里
这是完全不同的两段结论。依从度就是那个 manipulation check，也把
[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md) §4.5 的剂量-反应
从两点相关升级成中介链：**看了多久 → 被点名的弯真的动了 → 圈速**。

所以正确的做法是**跨 session**（baseline 建议 → 第二段行为），不是跨圈。

> 成本：一天。**已完成，见 §6。**

### D. confidence 是假的

- mock：`round(min(0.9, 0.5 + 0.8 * time_lost), 2)`（`coach.py:603`）——
  时间损失的线性函数，不是置信度。
- LLM 路径：模型自己填一个 0–1，然后散文被覆盖、**confidence 留着**。
- 界面把它渲染成绿色的 `high · 0.85` chip（`coach_panel.py:276`）。

**这是一个关于可靠性的断言，背后什么都没有。** 换成可算的（这个差值是该参与者
自己圈间散布的几个 sd），或者直接删掉。留着假的最差。

> 成本：半天。**已完成，见 §12 —— 是删掉。** 实测送到参与者眼前的 89 条 finding
> 全部落在 0.80–0.98，**整个研究里每一张卡片都是同一个绿色的 "high"**。而"换成可算的"
> 那条路走不通：散布在 coaching 这条路上拿不到（§10.6 的边界），何况 E 之后已经没有
> 剩余的梯度需要表达 —— 能上屏的 finding 都已经清过那个门槛了。

### E. 阈值是拍脑袋的常量，从没跟这个人自己的散布比过

`debrief.py:21-24`：

```python
NOTABLE_BRAKE_POINT_M = 10.0
NOTABLE_MIN_SPEED_KMH = 3.0
NOTABLE_THROTTLE_POINT_M = 10.0
NOTABLE_COAST_M = 15.0
```

注释自己写着"设在一般圈间散布之上"——但从来没量过任何人的散布。baseline 3 圈 +
第二段 3 圈是 6 个观测，够估一个粗糙的 per-corner sd。**这是 TSPulse 唯一真能加分
的地方**，见 §3.1。

> 成本：一天（含把阈值从常量改成"常量与个人散布取大"）。**已完成，见 §10。**
> 量出来的结果比预期严重：`min_speed` 的阈值比它自己的噪声底低 2.7 倍，而这个阈值
> 同时是排序用的除数 —— 所以它就是 §9 里 `min_speed` 统治一切的原因。落地时**没有**
> 改 `opportunity_catalog` 那一组（第四组阈值），理由见 §10.6。

### F. 参考圈选法太朴素，单圈模式几乎无话可说

参考圈永远是 session best（`coaching_queue.py:173`）。3 圈里的最快圈可能只是运气，
而且它自己也有烂弯。

后果最重的是**单圈模式**：最快圈那一圈是参与者最想听解释的一圈，却只有 4 个
technique check（`features.py:342` 的 `SINGLE_LAP_GUIDES`：coast / brake 次数 /
throttle 次数 / pedal overlap），完全没有弯速相关的建议。

合成参考圈（每个弯取该 session 最好的一次）能同时救这两件事。

> 成本：一天。**已完成，见 §8。** 注意落地时**没有合成任何一条圈**——合成会在接缝处
> 编造遥测。做法是"一条真圈当坐标系 + 每个弯取真实测量值"，理由见 §8.2。

### G. 语言 —— 清单里 ROI 最高的一条

全英文。而既然散文就是 15 条固定字符串（`llm.py:340-437`），**本地化成中文是改 15
个字符串，零幻觉风险，因为根本不经过模型**。

如果参与者里有中文母语者，现在的英文建议是一个理解力混淆项：读不懂建议和不采纳
建议在数据里长得一模一样。

> 成本：半天。~~注意 `_require_measurement_free_prose` 用 `character.isdigit()`
> 判断，中文全角数字（０-９）也会被 `isdigit()` 判为真~~
>
> **这一条 2026-08-28 换掉了做法，见 §11。** 本研究是英文授课项目，"参与者读不懂英文"
> 的前提不成立，而混着两种语言会让语言本身变成两条臂之间没控制的变量（问卷里根本没有
> 语言字段）。真正的问题不是语言而是**语域和硬件**：模板用的是事实核查器的措辞，而且
> 15 条里 14 条在要求踏板压力——参与者用的是键盘（实测，见 §11.2）。

### H. 曝光量仍然没记录

审计文件（`audit.py:write_coaching_audit`）记了 provider / model / prompt / 原始
回答 / 验证过的报告，但它记的是 **lap 文件名**，不记 participant / phase，也没有
任何"参与者实际看了什么"的记录。这是
[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md) §4.5 提的。

> 成本：1–2 天。**已完成（2026-08-27），见 §7；详细记录在姊妹文档 §11。**

### I. 延迟与资源

- `MANAGED_TIMEOUT_S = 900.0`（`coaching_queue.py:29`），CPU 上 3B，一次一个请求。
- `NarrationTask` 每次用完 `server.stop()`（`coach_task.py`），下次再花几十秒
  重新加载 2.1 GB。会话级 debrief 要连着问好几次，这个起停开销会被放大。

> 会话级 debrief 已经改成复用主窗口那台常驻 server（见 §5.3），不再自己起停。

### 另：一个已经修掉、但反复重演的 bug

~~`study_view.py:365` 仍然是 `summary_csv(self._summaries)`，没传 `backgrounds=`~~
—— 界面上看得见 Background 那一列，导出的 CSV 里那八列全是空的。这是
[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md) §4.4 / §6.1。

> **早已修好**（姊妹文档 §10.4）。但这个**形状**后来又出现了两次——剂量列一次、
> 依从度列一次——每次都是同一个函数调用少传一个可选参数，而表头看起来完全正常。
> 现在那个调用是四个参数齐全的 `backgrounds` / `exposure` / `adherence`，并且有
> 一条断言"每一行里这几列都非空"的守卫测试。见 §7.3。

---

## 3. 关于 Granite TSPulse R1 与 Granite Embedding 97M

### 3.1 TSPulse R1 —— 值得 spike，但先做成研究者工具，别急着进参与者包

原始想法里那句"**只能作为候选事件检测器，不能替代确定性指标**"是对的，而且这条
纪律在代码里已经是硬约束：

- `analysis/metrics.py:1-4`：*"if a number is not in this document, the coach may
  not say it"*
- `feedback/contract.py:valid_refs`：引用了不存在的 ref 的回答会被直接打回

**正确的接法**：TSPulse 提出 span → 现有确定性代码在那个 span 上算出**有名字、
有单位**的数 → 算不出东西就静默丢弃。异常分数本身永远不进 prompt，也永远不上界面。

**真正值得做的是它的第三个用途 —— 相似片段检索。** 那正好是 §2.E（噪声底）和
§2.F（合成参考圈）需要的原料。

两处要泼冷水：

1. **imputation 不要用在研究数据上。** `study.py:78` 的 `_blank` 立场是缺失报空白
   不报 0，理由写在 docstring 里：把没记录到的通道当成"没出界"，等于奖励数据不全
   的参与者。神经网络补出来的值同样不是测到的，而且比 0 更危险 —— 它看起来像真的。
   要用就只用在显示层，绝不进 `summarise_all` 那条路。
2. **工程成本才是真障碍。** `pyproject.toml` 现在的运行时依赖是
   `PySide6 / pyqtgraph / pandas / numpy / keyring` —— **没有任何 ML 栈**。
   TSPulse 要 PyTorch + granite-tsfm，等于给一个已经 1 GB 的参与者包再加
   2–2.5 GB，而且 PyInstaller + torch 在 Windows 上很难打。可行路径是导出 ONNX、
   只装 onnxruntime（约 50 MB）。

> 判断：先在研究者机器上做离线 spike，证明它能找出规则检测器
> （`analysis/events.py` 那五个）找不到的东西，再谈打包。

### 3.2 Granite Embedding 97M Multilingual R2 —— 先不做

坦白说：**现在没有一个检索问题。**

- **语料太小**：一个参与者 3 圈 × 2 phase；audit 记录里的"建议"总共就是那 15 条
  模板字符串。对 15 条固定字符串做语义检索，不如一次 dict 查表。
- **key 是结构化且精确的**："相似问题"在这个域里的正确键是
  `(corner, metric, 差值符号, 幅度)` —— 这些字段 `_corner_facts` 全都有。精确匹配
  比 embedding 近似匹配又快、又准、又可解释，而且在论文里说得清。
- **multilingual 解决的是跨语言匹配，不是生成中文**。要中文建议走 §2.G，不走
  embedding。

**什么时候它才值：** 出现了自由文本（设计文档里那个 stretch 的 Q&A 面板、赛后
问卷开放题、facilitator 笔记），并且语料跨参与者且足够大。等 §2.A 和 §2.C 做完、
真的攒下跨 session 历史了再回来看。

---

## 4. 建议顺序

| 顺序 | 做什么 | 为什么是这个顺序 |
|---|---|---|
| 1 | **A** 会话级 debrief 接到 GUI | 研究的干预本来就该是这个东西，代码已写好，缺的只是入口 |
| 2 | ~~**C** 跨圈依从度~~ **C** 跨 session 依从度 | 不是给参与者的反馈（见 §C 上方的更正），是研究缺的那个 manipulation check |
| ~~3~~ | ~~**G** 中文模板~~ **改为"人话 + 按设备措辞"，已完成（§11）** | 前提不成立；真正的问题是语域和硬件，不是语言 |
| ~~4~~ | ~~**D** 修掉假 confidence~~ **已完成（§12）** | 它现在是一个没有依据的可靠性断言 |
| ~~5~~ | ~~**B / F** 模式聚合与合成参考圈~~ **已完成（§8）** | 反馈质量的天花板在这里 |
| ~~并行~~ | ~~**E** 阈值对个人散布校准~~ **已完成（§10）** | 量完才发现它不是打磨项：阈值同时是排序除数，错的那个决定了大部分卡片说什么 |
| 并行 | TSPulse 离线 spike | 放研究者机器上，不进参与者包 |
| 暂缓 | Granite Embedding | 等有了自由文本语料再说 |

---

## 5. 进展：A 已完成（2026-08-25）

**会话级 debrief 现在是 Apex 的一个界面。** 参与者跑完一段之后能在 Garage 里点开
"Session debrief"，看到整段 session 每一圈对着最快圈的差距、最贵的那几段路，以及
（模型跑得动的时候）针对每一段的一句话建议，还能把它存成 markdown 带走。

### 5.1 改了什么

| 文件 | 改动 |
|---|---|
| `src/racecoach/granite/report.py` | 把 `build_report` 拆成"先量完、再叙述"，加两个可选回调 `on_measured` / `on_narrated`；新导出 `measure_report` |
| `src/apex/debrief_view.py`（新） | `SessionDebriefView` + `_DebriefTask` + `_LapCard` |
| `src/apex/garage_view.py` | 新按钮 **Session debrief** 和 `debriefRequested` 信号 |
| `src/apex/main_window.py` | 新视图接进 stack、工具栏多一项 **Session Debrief**、删除 session 时清掉它 |
| `tests/test_debrief_view.py`（新）、`tests/test_granite_report.py` | 14 条新测试 |

**没有新增任何一个算法。** 这次改的全部是入口和呈现：debrief 的内容仍然是
`f1coach_core.debrief` 量出来的、`racecoach.granite.narrate` 叙述的那一份，和
`racecoach debrief` 命令行写出来的**是同一份** —— 有一条测试就在断言这个：
`view.save_debrief(...)` 的内容等于 `render_markdown(view.report)`。

### 5.2 为什么要拆 `build_report`

原来测量和叙述在一个循环里交替进行，界面要么等到全部叙述完才能显示，要么什么都
显示不了。CPU 上一次叙述要几分钟，一段 session 要问好几次 —— 参与者盯着空面板等
五分钟，得到的结论是软件坏了。

拆开之后：**数字先到，文字后落。** 所有圈量完 `on_measured` 一次，之后每叙述完一圈
`on_narrated` 一次，界面逐圈把话补上去。跑不动模型的笔记本拿到的是一份完整的
debrief，只是没有散文 —— 而不是一份空的。

命令行那条路一个参数都没改：两个回调都不传时行为与改动前相同，有测试锁着
（`test_callbacks_are_optional_and_change_nothing`）。

### 5.3 几个刻意的决定

- **共用主窗口那一台 model server 和那一个 worker。** `SessionDebriefView` 拿的是
  `MainWindow._coaching_server` 和 `_coaching_pool`（`maxThreadCount == 1`）。参与者
  读 session debrief 的同时 Garage 还在逐圈分析，两件事必须排队，而不是变成一台
  笔记本上同时跑两个 3B 模型。视图**不会** `stop()` 那台 server —— 它的生命周期归
  窗口管。
- **打开界面永远不会触发下载。** 和 `CoachPanel._auto_run` 同一条纪律：`capability`
  说需要下载时，这个界面只说明情况并把人指向 Lap Analysis（下载和它的取消按钮在
  那里），自己不长第二个进度条。
- **没有接到采集完成页上。** 那一页刻意把交接说明留在屏幕上，而且对照组（`control`）
  参与者按设计就不该拿到 AI 建议 —— 在那一页自动弹出 debrief 会让分组纪律取决于
  参与者手快不快。入口放在 Garage，由 facilitator 决定什么时候打开。
  （注：这一点与现状一致，不是新增风险 —— Garage 里的逐圈 AI 分析本来就对所有人
  可见。要真正堵住得按 phase 门控，那是另一件事。）
- **回到同一个 session 不重算。** 重新测量会把几分钟才拿到的散文丢掉，换回一模一样
  的数字。

### 5.4 验证

- **656 passed / 17 skipped**（改动前 642 / 17），`ruff check src tests` 干净。
- 新测试里真正吃劲的几条：模型完全不存在时数字照样齐全并说明原因；打开界面不会
  启动下载（`server.starts == 0`）；server 起不来时仍然出完整测量并把原因写在状态行
  上；叙述落到测量之上而测量不变；存出来的文件等于命令行写的那一份；Garage 的按钮
  真的把整段 session 送到了新界面（走 `MainWindow`，不是直接调视图）。
- 关于"先在旧代码上跑一遍确认失败"：这次**没做**，因为新增的是一个原本不存在的
  模块，旧代码上的失败是 ImportError，证明不了行为 —— 和 §9.3 记的是同一个理由。
  真正的证据是结构性的：旧 `MainWindow` 里没有任何东西接收 session 级报告，
  `racecoach.granite.report` 的唯一调用者是 `cli.py:436`。
- 离屏渲染检查过布局：标题 / 参与者行 / 两个按钮 / 状态行 / 可滚动的每圈卡片，最快
  圈的标题是紫色，与 Apex 其它地方"紫色 = session best"一致。offscreen 平台没有
  字体，截图上是方块，那是渲染环境不是布局。

### 5.5 打包

参与者包 `repos\Apex` 已重新打包换上并验证：

- 新 `Apex.exe` 里 `SessionDebriefView`、`debriefRequested`、`measure_report`、
  `_DebriefTask`、`DOWNLOAD_ELSEWHERE`、`Session debrief`、`on_measured`、
  `_open_debrief` 八个标记全在；被替换的那个**一个都没有**。
- `_internal` 逐个哈希比过：1074 个文件，只有 `base_library.zip` 因重打包时间戳不同，
  已一并换掉，没有 EXTRA。
- 备份留在 `Apex.exe.bak-20260825-1840` / `racecoach.exe.bak-20260825-1840`。
- `racecoach.exe --help` 退出码 0；`Apex.exe` 启动出窗口（标题 'Apex'）并干净退出。

**一个环境上的坑，值得记下来**：这台机器是 AVD，`C:\Users\hh25303` 位于一个 39 GB 的
FSLogix 配置卷上，而 `Get-PSDrive`、`fsutil volume diskfree <路径>` 和
`Scripting.FileSystemObject` **全都报的是 C: 的 86 GB**。配置卷满的时候 PyInstaller
写 `xref-apex.html` 时以 `OSError: [Errno 28] No space left on device` 失败，看起来
毫无道理。唯一说实话的是 `Get-Volume`，按 `FileSystemLabel` 找 `Profile-*`。这次是
清掉 pip 缓存和 `build\` 之后腾出的空间，并把构建产物改到 AVD 的临时盘：
`pyinstaller apex.spec --workpath D:\apex-build\work --distpath D:\apex-build\dist`。

---

## 6. 进展：C 已完成（2026-08-27）

**"参与者有没有照着建议做"现在是一个可以导出的量。** 新模块
`f1coach_core/adherence.py` 把一段 baseline 的 debrief 拆成若干条结构化的"要求"
（哪个弯、哪个指标、要往哪个方向动、动多少才算数），再拿参与者第二段的实际驾驶去对。
结果同时出现在 `racecoach study-summary` 的每行末尾，和新命令
`racecoach study-adherence` 的逐条明细里。

### 6.1 改了什么

| 文件 | 改动 |
|---|---|
| `src/f1coach_core/adherence.py`（新，约 380 行） | `Prescription` / `Shift` / `AdherenceReport`，`prescriptions` / `run_prescriptions` / `adherence` / `run_adherence` / `session_adherence` / `adherence_all`，两组导出列 |
| `src/f1coach_core/debrief.py` | `_candidates` 改成 `_Gap` NamedTuple，多带指标名和有符号差值；`DebriefPoint` 新增 `metric` / `gap`（都有默认值，纯增量） |
| `src/f1coach_core/study.py` | `summary_columns` 加 `ADHERENCE_COLUMNS`；`summary_csv` 加可选 `adherence=`；新增 `adherence_csv` |
| `src/racecoach/cli.py` | 新命令 `study-adherence`；`study-summary` 自动带上依从度 |
| `tests/test_adherence.py`（新） | 31 条测试 |

### 6.2 为什么不是"上一圈 vs 这一圈"

这是这次唯一真正重要的设计判断，理由写在 §C 上方的更正里，不重复。落到代码上只有
一句话：**每一次比较都跨越两段 run，因为那是建议唯一被送到过参与者手上的地方。**
模块 docstring 里把这句话写成了硬性约束，免得以后有人"顺手"加一个同段内的比较。

同段内的圈间变化并没有被丢掉——它变成了**噪声底**，见 6.3 第三条。

### 6.3 三个让比较站得住的决定

**一、跨 session 不能按弯角名字匹配。** `detect_corners` 是按检测到的 apex 顺序
编号 `T1..Tn` 的（`features.py:99`）：在两条不同的圈上各跑一次检测，只要多出或漏掉
一个 dip，后面每一个名字都会错位——baseline 的 T3 会悄悄变成第二段的 T4。所以两段的
**每一圈都在同一条锚定圈的弯角网格上测量**，而这条锚定圈就是 debrief 本身所对的
参考圈（`report.py:81` 和 `coaching_queue.py:172` 都取 session 最快圈）。弯角同一性
因此是**按构造成立**的，不是靠运气。传错锚定圈会抛 `AdherenceError` 而不是默默算出
一堆没人看得出错在哪的数。

**二、跟踪绝对值，不是 gap。** debrief 说的是差值（"刹车早了 12 m"），而差值的参照系
在第二段换成了另一条圈。参与者实际把刹车踩在哪里是他自己的行为，同一条赛道上跨段可比。

**三、拿参与者自己的散布当尺子。**"刹车点动了 16 m"单独没有意义：对一个在那个弯
圈间飘 20 m 的人这是噪声，对一个能重复到 3 m 以内的人这是决定。所以每一条都同时报
baseline 三圈在同一弯同一指标上的 `pstdev`，以及 `shift_sd = 位移 / 散布`。

### 6.4 真实数据当场推翻了我原来的判罚规则

模块写完时，二值 `followed` 只用固定阈值（`NOTABLE_*`），docstring 里我还给了理由：
三圈估出来的 sd 自带约一半的误差，让二值依赖一个噪声很大的分母，不如用一个钝一点的
固定值。

**拿 B0826 真实数据一跑就塌了：**

```
T6  min_speed_kmh   toward 3.3   in own sd 0.3   followed True
```

`NOTABLE_MIN_SPEED_KMH` 是 3.0 km/h，而这位参与者在 T6 的圈间散布是 **11 km/h**。
动了 0.3 个 sd 被判成"照做了"。固定阈值在这里不是"钝"，是**尺度就是错的**，于是
自信地判错。

改成 `required = max(固定阈值, 该参与者自己的 sd)`。关键是这个方向的不对称性：
**散布只可能把门槛抬高**，所以三圈 sd 的误差永远不可能凭空造出一个"照做了"的参与者，
最坏只是漏掉一个真的。对 manipulation check 来说这正是想要的方向——一个错误相信
自己的建议被采纳了的研究，后面每一句话都没法解释。改完 T6 正确判为 False。

这也顺手把清单里的 **E**（阈值从没跟个人散布比过）做掉了一半：至少在依从度这条路上，
阈值现在是"常量与个人散布取大"。

### 6.5 在你已经采到的数据上的第一个结果

把 `win_collect_data` 里的 handover 导进一个隔离的 `APEX_WORKSPACE`（没有碰
`~\Apex` 里参与者的任何东西）之后：

| driver | phase | best_lap_s | prescribed | measured | followed | rate | mean shift_sd |
|---|---|---|---|---|---|---|---|
| B0826 | baseline | 119.616 | | | | | |
| B0826 | coached | 126.120 | 4 | 4 | 1 | 0.25 | 0.67 |
| C0826 | baseline | 105.980 | | | | | |
| C0826 | control | 114.186 | 4 | 4 | 0 | 0.0 | −0.65 |

（baseline 行按设计留空：那时候还没有人对他们说过任何话，这跟"他们没照做"不是一回事。）

两件事值得单独说：

1. **两位参与者第二段都变慢了**（+6.5 s / +8.2 s）。n=2 的先导数据，不宜多解释，
   但它正好说明为什么需要这个量：如果只看圈速，你分不清是建议有害、还是别的原因。
2. **B0826 唯一照做的那一条（T1 弯中最低速 +19.1 km/h，3.83 个自身 sd）幅度很大，
   而他整体还是慢了。** 这是那种"没有依从度就完全看不见"的结构。

### 6.6 验证

- `731 passed / 17 skipped`（接手时基线是 `700 / 17`，我加了 31 条），`ruff check
  src tests` 干净。
- 端到端在真实 handover 上跑通：`racecoach study-adherence` 和
  `racecoach study-summary` 都输出了上面的表。
- `debrief.py` 的改动是纯增量的（两个带默认值的字段），122 条涉及 `DebriefPoint`
  的既有测试全绿。
- 弯角错位这个最危险的失败模式有专门的测试守着：传一条不含被建议弯角的锚定圈会抛
  `AdherenceError`。

### 6.7 还没做的

- **没有做参与者可见的界面。** 按 §C 的结论，在两段式流程里它对驾驶提升为零，
  所以先不做；真要做，也应该说清楚它是"闭环告知"而不是干预。
- `adherence_all` 里的 `BASELINE_PHASE = "baseline"` 是写死的。目前研究只有这一个
  参照相，改协议的话这里要跟着改。
- 依从度只进了 `summary_csv`，没进 `lap_csv`：它是 run 级的量，逐圈没有意义。

---

## 7. 进展：H 已完成（2026-08-27，另一个会话做的）

**完整记录在姊妹文档
[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md) §11**
（那边它叫 6.5），写得很细，这里不重复。这一节只补这份文档自己关心的四件事。

### 7.1 §2.H 那两条都补上了

- **"审计记录只记 lap 文件名"** → `audit.py:write_coaching_audit` 多了 `driver` /
  `phase` / `setup` 三个字段，从 `lap.identity` 取。
- **"没有任何参与者实际看了什么的记录"** → 新模块 `f1coach_core/exposure.py`
  （369 行，Qt-free）。两种 view：`corner`（review 窗口开在某个弯上）和 `report`
  （分析页右侧 findings 面板）——分开记，因为"把面板读完了但一个弯都没点开"是真实
  存在的一类人，合并成一个数就把这类人藏了。五个剂量列进了
  `study-summary` / `study-laps`，外加新命令 `study-exposure` 出逐条原始记录。

### 7.2 H 和 C 现在合成一条完整的链

**这一句两份文档都还没写**，因为 C 是在 §11 落笔之后才做的：

```
看了多久（H）→ 被点名的弯真的动了（C）→ 圈速（原有）
```

单独看，H 是"剂量 vs 结果"的两点相关，C 是"建议 vs 行为"孤立的一环。合起来才是
中介链，而这正是 n 很小的时候唯一还站得住的因果论证形状。

导出里两者是相邻的列，`summary_columns()` 的顺序就是这条链本身：

```
PERFORMANCE → EXPOSURE → ADHERENCE → BACKGROUND
```

一个分析的人从左往右读一行，读到的就是完整的论证。

### 7.3 顺手修了一个我自己刚制造出来的同类 bug

§11.6 明确警告过"§4.4 那个 bug 不能以新的形式重演一遍"——**而我做 C 的时候正好
重演了它**：CLI 那条路接上了 `adherence=`，GUI 的 `Export CSV…` 没接，于是界面上
多了五列、导出的文件里那五列全是空的。

已修（`study_view.py:_export_csv`），并按 §11.7 的做法红过再绿：把
`adherence=adherence_all(self._laps)` 那一行删掉，新加的守卫测试报

```
ValueError: invalid literal for int() with base 10: ''
```

这个 bug 形状出现三次了（背景列、剂量列、依从度列），每次都是**同一个函数调用少传
一个可选参数**。守卫测试现在断言的是"这几列在每一行里都非空"，不是"表头里有这几列"。

### 7.4 §2 里还有两处话已经过时

- **§2.H 的"至今没做"** —— 已在本节标注。
- **"另：一个仍未修的 bug"** 里说 `study_view.py:365` 不传 `backgrounds=` ——
  那条更早就修好了（姊妹文档 §10.4）。现在那个调用四个参数齐全：
  `backgrounds` / `exposure` / `adherence`。

### 7.5 验证

- `734 passed / 17 skipped`，`ruff check src tests` 干净。
  （分段读：H 之前 698 → H 之后 700 → C 之后 731 → 加上 7.3 的守卫 734。）
- 参与者包已由另一个会话在 2026-08-27 10:57 重新打包换上，我按老办法验过：
  新 `Apex.exe` 里 A / C / H 三组标记**全在**（7+6+3），被替换的
  `Apex.exe.bak-20260827-0955` **有 H 没有 C**——这正好证明 10:57 那次构建就是
  把 C 装进去的那次。`racecoach.exe` 缺 `SessionDebriefView` 和 `_open_debrief`
  是对的，CLI 本来就不含 Qt 视图。
- 冻结的 exe 上跑通：`racecoach.exe study-adherence --help` 退出码 0；
  `Apex.exe` 在隔离 workspace 下启动出窗口（标题 `Apex`）并被干净结束。
  `_internal` 1074 个文件 / 195 MB。

> **注意 7.5 最后这条的分量**：姊妹文档 §11.8 那个 BOM 缺陷就是**跑冻结的 exe 才
> 抓到的**，读代码读不出来。"源码测试全绿"和"参与者手上那个包是对的"是两件事。

---

## 8. 进展：B 与 F 已完成（2026-08-28）

两条一起做，因为它们落在同一个地方。**F** 让参考不再是"一条可能只是运气好的圈"，
**B** 让"你在大部分弯都这样"这句话第一次有人说。在真实数据上，两者最终都命中了
**同一圈**——B0826 coached 那一段的最快圈——而那正是旧代码最没话可说的一圈。

### 8.1 改了什么

| 文件 | 改动 |
|---|---|
| `src/f1coach_core/reference.py`（新，334 行） | `CompositeReference` / `CornerBest`；`composite_reference` / `corner_measurements` / `composite_corner_facts` / `composite_corner_table` / `composite_evidence_summary` / `composite_debrief` / `composite_review_points` / `composite_summary`；`evidence_for` / `reference_name` 两个统一入口 |
| `src/f1coach_core/features.py` | `NOTABLE_*` 四个阈值与四个类别名搬到这里（`debrief` 改为导入并再导出）；抽出 `_zone_indices`；新增 `corner_patterns` 与 `PATTERN_METRICS` |
| `src/f1coach_core/debrief.py` | 抽出 `debrief_points` / `review_points`（吃事实、不吃圈），`lap_debrief` / `corner_review_points` 改为转调 |
| `src/f1coach_core/adherence.py` | `run_prescriptions` 补上最快圈那一份建议，见 8.6 |
| `src/f1coach_core/audit.py` | 改用 `evidence_for` / `reference_name`，两条路都能记 composite |
| `src/f1coach_core/llm.py` | 新增 composite 的 prompt 语境；带上 `corner_delta_s` |
| `src/f1coach_core/report.py` | HTML 导出认识 composite：图表退回单圈，引用照实标为比较 |
| `src/racecoach/granite/report.py` | 会话级 debrief 的最快圈不再只有一句恭喜 |
| `src/apex/coaching_queue.py` | 最快圈排的是 composite，不是 `None` |
| `src/apex/coach_panel.py` | `PatternBanner`；接受 composite 上下文；引用标签 `vs best` |
| `src/apex/analysis_view.py` | 参考下拉多一项"Best at each corner"，最快圈默认选它；弯角表与 debrief 走 composite |
| `src/apex/captions.py` | `composite_caption` |
| `tests/test_reference.py`（新，16 条）、`tests/test_patterns.py`（新，15 条） | 加上 `coach_panel` 6 条、`granite_report` 3 条、`adherence` 2 条，共 **42 条新测试** |

### 8.2 F：为什么不是"合成一条圈"

§2.F 的原话是"合成参考圈"。**没有合成任何一条圈**，而且这是刻意的。

把几条圈按弯角拼成一条新的 dataframe，就得在接缝处编造速度、时间和踏板值——一份
看起来像遥测、但没有任何一次真实驾驶产生过的数据。这个项目其它地方在这件事上的
立场很硬：`analysis/metrics.py` 开头写着 *"if a number is not in this document,
the coach may not say it"*，`study.py:_blank` 宁可留空也不填 0。合成圈会把这条线
从后门绕过去。

所以真正做的是：**保留一条真圈当坐标系（anchor），每个弯的目标值取该弯开得最快的
那一圈的实测值。** 每一个 `ref_*` 都是某条圈真的记录过的数，而且每一行都带
`ref_source` 说明是哪一条。少了接缝，也少了一整类"这个数从哪来的"的问题。

三条纪律（和 `adherence` 是同一套，理由也一样）：

1. **弯角只在一条圈上检测。** `detect_corners` 按检测顺序编号，`T4` 的意思只是
   "这条圈上的第四个 dip"。在两条圈上各检测一次再按名字配对，多一个 dip 就会让后面
   每个名字错位。所以所有圈都在 anchor 的弯角网格上量。
2. **"这个弯谁最快"按分区用时判，不按圈速判。** 圈速快不代表每个弯都快，这正是
   §2.F 抱怨的那件事。
3. **它按"真的贡献了弯角的圈"计数。** 界面上叫 `Best at each corner (3 laps)`，
   审计里叫 `best corners of 3 laps`——即使那一段有 5 圈。一条处处更慢的圈既不改变
   任何目标值也不改变 evidence packet，所以它也不该改变名字：名字一动，
   `latest_coaching_report` 就会为一个根本没变的比较错过已经存在的报告，把参与者
   送回去等模型算一遍磁盘上已经有的答案。
4. **它没有圈速，所以它不报圈速。** `lap_time_s` 和 `total_delta_s` 都是 `None`；
   报的是 `corner_delta_s`——被测弯角里可拿回的时间，这是唯一被真的量到的东西。
   把几段最好的拼起来当成一个圈速写在屏幕上，参与者拿自己看得见的成绩一对就知道
   对不上，然后整块屏幕的可信度一起没了。

### 8.3 F：最快圈那一圈现在有话说了

§2.F 说"后果最重的是单圈模式"。在自带样例上，前后是这样：

```
改前（single_lap）：T8 coast_distance 105 m vs 20 m（一个写死的 guide）
                    T9 brake_applications 3 次 vs 1 次
                    T1 brake_applications 2 次 vs 1 次

改后（composite）： T5 exit_speed  161.2 km/h vs 211.3 km/h  ← lap01 真开出来过
                    T6 min_speed    20.4 km/h vs  33.8 km/h  ← lap01
                    T8 full_throttle 1910 m   vs 1845 m      ← lap05
                    整段：2.87 s，分布在 10 个弯里的 5 个
```

左边那三条的参照是常量；右边这三条的参照是这位参与者自己在同一场里开出来过的。
后者可达性是被证明过的——**有人真的那样开过那个弯**，而且就是他自己。

改动只落在**最快圈**上：其余每一圈仍然对着 session best。理由是这样两边不会打架
——Garage 后台排的队和 Analysis 界面打开时用的上下文必须一致，否则 Granite 会在
参与者的 CPU 上为同一圈跑第二次（几分钟）。最快圈原本的上下文是"没有上下文"，换掉
它不会让任何已经算好的分析失效。

界面上：参考下拉多了一项 **Best at each corner (N laps)**，最快圈默认选它。
选中时曲线区不叠加任何参考——composite 没有一条可画的轨迹，而这一圈原本也就没有
叠加——但**弯角表、debrief 文字、AI 面板**三样都换成了真比较。
会话级 debrief（§5 那个界面，也就是研究的干预本身）同样：最快圈从"这是你最快的一圈"
变成了三条可看的地方。它在屏幕上仍然是紫色标题，`is_reference` 现在问的是"这一圈
是不是整段的基准"而不是"它是不是它自己的参考"。

### 8.4 B：真实数据当场推翻了对称报告

第一版 `corner_patterns` 是**对称**的：差值往哪边跑都算一种 pattern，措辞跟着变。
理由当时看着很对——`debrief.py` 就是这么做的，它明确拒绝断言因果，只报"量到了什么"。

**拿 `win_collect_data` 里 15 圈真实数据一跑就塌了：**

```
B0826 coached lap01  ->  Going FASTER through the slowest part of most corners  [5 of 7]
B0826 coached lap02  ->  Going FASTER through the slowest part of most corners  [5 of 7]
```

这两圈是这位参与者**最慢的两圈**。而 banner 的位置在 finding 卡片**上方**——那是一个
人找"我该改什么"的地方。放在那里的一句表扬，比什么都不说更糟。

问题出在照搬：`debrief.py` 是在**描述一圈**，可以两边都报；banner 是在**给建议**，
位置决定了它会被这样读。§2.B 自己的例子其实早就写明了方向——"一个每个弯都**刹早**
的人"。刹早是毛病，刹晚不是。我做的时候把方向丢了。

改成**只报会损失时间的那个方向**，四个指标的方向与阈值直接取 `opportunity_catalog`
教练用的那一套，这样 pattern 和 finding 不可能对同一个弯指相反的方向。反方向的弯
仍然数，记成 `against`——它不是另一个 pattern，它是"这个说法有多干净"的证据。

改完，同样 15 圈真实数据里只剩 2 条 pattern：

```
0823 baseline lap01   Braking earlier than the reference at most corners
                      [4 of 6 corners, typically 25 m earlier]
B0826 coached lap03   Carrying less speed through the slowest part of most corners
                      [5 of 7 corners, typically 24 km/h slower]
```

第一条**逐字就是 §2.B 举的那个例子**。第二条落在那一段的最快圈上——也就是 §8.3 里
改前"只有四个 technique check"的那一圈：B 和 F 在同一圈上会合。

15 圈出 2 条，这个稀疏程度是想要的：**banner 出现才意味着什么。** 门槛是
`measured ≥ 4`、`agreeing ≥ 3`、`share ≥ 0.6`，三个数都写成了常量、有测试盯着，
将来要改可以直接对着数据吵。

### 8.5 B：为什么不是 contract 里的 `scope: "session"` finding

§2.B 的成本行提的是"`coach.py` 的 contract 加一类 `scope: "session"` 的 finding"。
**没有这么做**，理由是走那条路会把这句话的内容切掉一半：

- `_require_measurement_free_prose` 不许 prose 里出现任何数字（连全角数字都算），
  所以 **"6 个弯里的 4 个"这个数根本没地方放**——而那个数就是这句话的全部分量。
- citation 只能引 `opportunity_catalog` 里的 `(弯, 指标)`，而那个目录只包含
  `coachable_corners` 挑出的前 6 个弯。pattern 的意义恰恰在于它覆盖了**没被出卡的
  那些弯**，限制在前 6 个就把它变成了三张卡的第四份拷贝。
- finding 是"某一段路上的一个断言"，配一个跳过去的 `◈ show` 按钮。pattern 没有可跳的
  地方。做成第四张卡会让人去找"它说的是哪个弯"。

所以 pattern 走的是自己的通道：确定性算出来，`PatternBanner` 单独渲染（左侧一道
类别色边、`ACROSS CORNERS` 标签、无 focus chip 无 confidence chip），LLM 的 schema
和校验器一行未动。审计也没丢——pattern 是 `evidence_summary["corners"]` 的纯函数，
审计记录里那份 corners 原样保存着，任何时候都能重算出完全一样的结果。

**并且刻意没有把它写进 evidence packet。** `latest_coaching_report` 是拿整个 packet
做全等匹配的：多一个 key，所有人已经等模型跑出来的分析全部作废、下次打开逐圈重跑。
一个能重算的派生量不值这个代价。（对照 §7.3 的教训：这次是同一类"两条路必须一致"的
问题，但结论相反——不是漏传参数，而是根本不该存。）

### 8.6 一个必须跟着改的地方：依从度的账本

`adherence.run_prescriptions` 的 docstring 写着 *"Mirrors `measure_report`: every lap
against the run's fastest, **the fastest lap having nothing to lose to itself**"*，
代码里就是 `if lap is anchor: continue`。

**F 把这句话变成假的了。** 最快圈现在有卡片了，参与者会读到，而账本还在跳过它——
于是"他被告知了什么"少记了一部分，manipulation check 会**悄悄少报**。数字看着照样
合理，只是描述的范围比它声称的小。这正是 §7.3 那个 bug 的形状第四次出现。

已修，并且加了一条比数数更强的守卫：

```python
assert run_prescriptions(laps) == prescriptions(shown)   # shown 来自 measure_report
```

断言两边**完全相等**，而不是断言某个数量。这样它对将来任何一边报什么的改动都还成立，
而它正好会在"某一圈被结构性地漏掉"时红。红过再绿：把 `if lap is anchor: continue`
放回去，两条测试立刻失败。

### 8.7 在真实数据上的结果，和一个必须说清楚的坑

同一批 handover，同一套隔离 workspace（`~\Apex` 一个字节没碰）：

| driver | phase | prescribed | measured | followed | rate | shift_sd |
|---|---|---|---|---|---|---|
| B0826 | coached | 4 → **7** | 4 → **7** | 1 → **3** | 0.25 → **0.429** | 0.67 → **0.90** |
| C0826 | control | 4 → **6** | 4 → **6** | 0 → **0** | 0.0 → **0.0** | −0.65 → **−0.46** |

可测的建议条数涨了 50–75%，全部来自"每段的最快圈现在也出卡片了"。对一个 n 很小的
研究，这是直接的收益：同样的两个人，依从度是在更多条建议上估出来的。

> **坑：这两行的新旧数字不可混用。**
> B0826 和 C0826 是 2026-08-26 采的，跑的是**旧版本**——他们看到的最快圈那张卡上
> 写着"This was your quickest lap of the session."，没有任何建议。而
> `run_prescriptions` 是**重算**的（它必须能处理没有审计记录的旧数据），所以在新版本上
> 重算，会把他们从没看见过的建议算进"他被告知的"里面。
>
> 也就是说：**上面的"改后"三列对这两位先导参与者是高估的**，而对正式研究里用新包采的
> 数据是正确的。规则很简单——**一批数据用哪个版本采的，就一直用哪个版本算依从度，
> 不要跨版本重算，也不要把跨版本的两批放进同一张表。**（先导数据的原始数字保留在
> §6.5，不要覆盖。）

### 8.8 验证

- **782 passed / 17 skipped**（接手时 740 / 17，加了 42 条），`ruff check src tests` 干净。
  分段读：740 → 加 `test_patterns` 15 → 加 `test_reference` 16 →
  面板 6 + 会话报告 3 + 依从度 2 = 782。另有 3 条既有测试改了断言，全部是 F 故意改掉的
  行为（队列给最快圈的参考、下拉项数、`test_window` 里那条改名为
  `test_session_best_opens_against_its_own_corners_not_against_nothing`）。
- **红过再绿**，四处分别验过：把 pattern 改回对称报告 → 方向测试与散布测试红；
  把会话 debrief 的 composite 关掉 → 最快圈测试红；把 banner 从 `_clear_cards` 里
  摘掉 → 堆积测试红；把 `if lap is anchor: continue` 放回 → 依从度两条守卫红。
- **真实数据跑通**：`racecoach study-summary` 在 15 圈真实 handover 上输出上表；
  pattern 规则在同样 15 圈上从 5 条（其中 4 条是表扬）收到 2 条（都可执行）。
- **离屏渲染检查过版式**：banner 在三张卡之上，左侧红色类别边，`ACROSS CORNERS`
  小标签、加粗结论、暗色明细行；没有 focus chip 也没有 confidence chip，一眼看得出
  它和下面三张不是同一种东西。（offscreen 平台没有字体，截图上是方块，与 §5.4 同因。）
- **既有审计记录仍然有效**：comparison 模式的 evidence packet 一个 key 都没变
  （见 §8.5），所以已经算好的逐圈分析不会因为这次改动重跑。唯一会重跑的是每段的
  最快圈——它的参考从 `null` 变成了 `best corners of N laps`，那正是这次要改的事。

### 8.9 还没做的

- **pattern 没有进会话级 debrief。** 那是研究真正的干预界面，价值最高的落点。
  一开始没做是因为 `apex/debrief_view.py` 被另一个会话持有；那个理由在 08-28 下午
  已经不成立了（文件放开了），**但仍然不做**，换成一个更好的理由：往那块屏幕上加
  内容就是在改自变量，而现在正是不该改它的时候，见 §8.10。等这批数据收完再说。
  （`render_markdown` 那条路本可以单独加，但那会让 markdown 和界面说的不一样，
  比两边都不说更糟。）
- **非最快圈仍然对着 session best**，不是 composite。理由在 §8.3：换掉会让曲线区
  失去参考叠加，并让所有已算好的分析全部重跑。代价是 §2.F 抱怨的"最快圈可能只是
  运气"对其余圈只解决了一半。
- **单圈模式没有 pattern。** 单圈模式的 `ref_*` 是写死的 guide，"你在大部分弯都比
  guide 差"是在拿人跟一个常量比，不是跟驾驶比。F 之后这个模式本来也少见了——
  最快圈不再落到它里面。
- **`MAX_PATTERNS_SHOWN = 2`** 是界面上的取舍，不是测量上的：`corner_patterns` 返回
  全部合格的 pattern，研究侧拿得到完整数据。

### 8.10 一件要你决定的事：这批改动动了参与者读到的东西

姊妹文档 [STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md)
**§12.8** 已经把这件事写成了三个选项和一条建议（建议是"先不发"），不重复。这一节
只补一件那边没覆盖到的事：**范围比那边写的宽。**

§12.8 说的是 `racecoach/granite/report.py` 改了 session debrief 的内容。那是真的，
但同一批改动还动了 **Lap Analysis 那块屏幕**，而那块屏幕参与者一样看得到——它是从
Garage 点开一圈进去的（`main_window.py:100`），而且它右侧面板的阅读时长就是 §2.H
记的那个剂量（`analysis_view.py:453` 的 `REPORT_VIEW`）。

完整清单：

| 参与者看得到的地方 | 改前 | 改后 |
|---|---|---|
| Session debrief 屏（干预本身） | 最快圈一句恭喜、零条发现 | 最快圈有发现，也会被模型叙述 |
| Lap Analysis 的 AI 面板 | 最快圈 4 条写死的 technique guide | 最快圈引用自己其它圈的实测值 |
| Lap Analysis 的 AI 面板 | — | **新增 pattern banner**：一句从来没有参与者见过的话 |
| Lap Analysis 的弯角表 / debrief 抬头 | 最快圈是单圈表，没有抬头 | 多一列 Δ，多一行抬头 |
| Garage 的 `ANALYSED · n findings` | 最快圈的 n 来自单圈技术检查 | 来自 composite |

**所以除了自变量不一致，还有一层：剂量的单位也变了。** `report_seconds`
（`exposure.py:phase_exposure`）量的就是这块面板在参与者眼前多久。一块多了 banner、
把四条写死的 guide 换成三条真发现的面板，本来就该被读得更久。同一个数字在改前改后
不是同一件事的度量，跨版本放进同一列会把"内容变多了"读成"参与者更投入"。

> 更正：这一段先前写的是 `advice_seconds`，那是错的。`advice_seconds` 加的是
> **corner view**（review 窗口里屏幕上有 AI 建议的那些秒），不是这块面板。会被这次
> 改动抬高的是 `report_seconds`。结论不变，列名换一个。

**而且事后分不出来。** 一条 `ReviewView` 只有十个字段
（`id / at / driver / phase / kind / lap / corner / seconds / advice / findings`），
**没有一个记应用是哪一版**——`SCHEMA_VERSION = "apex-exposure-v1"` 版本化的是日志
文件格式，不是构建。而 `findings` 数的是 finding，**banner 不计**
（`analysis_view.py:452` 的 `count = len(findings)`）。所以"三条发现 + 一条 banner"
和"三条发现"在日志里是**完全相同的一行**。真要中途发，最低前提是先把构建标识写进
曝光记录；没有它，跨版本的 `report_seconds` 不是"要小心",是**不能用**。

**还有一个 banner 自己带出来的缺陷**：`advice=count > 0`，而 `count` 同样只数 finding。
`coach_panel._nothing_found()` 有一条刻意的分支——没有任何一个弯单独达标、但 pattern
成立时，屏幕上是"一条 banner + 一句『没有哪个弯特别突出，上面那条就是要说的』"。
那一刻参与者**正在读一句教练建议**，而这条曝光记录写的是 `advice=False`、`findings=0`。
这是新加的 banner 造成的，不是既有缺陷。真要发，这个也得一起修——让 banner 计入
`advice`，并且单独记一个 banner 数，否则连"他看的时候屏幕上有没有建议"都记错了。

它的影响范围要说准，免得两边都误判：**原始日志错，逐条导出错，汇总列不受影响。**
`study-exposure` 是逐条写的（`study.py:459` 的 `row["advice"] = 1 if view.advice else 0`），
所以错值会进到一个真的产物里；而汇总里唯一读 `advice` 的是 `advice_seconds`，它只看
corner view（`exposure.py:407`），banner 是 report view，进不去。也就是说：既不能当成
"只是内部状态"不管，也不必去汇总表里找一列被它污染的东西——那一列不存在。

我在 §8.7 提醒过依从度不要跨版本重算——那是**测量侧**。上面这些是**干预侧**，比那条重：
测量可以在分析机上重算，已经发生的干预不能。§8.7 那段没有说到这一点，是我漏了。

现状，说清楚免得后面有人搞错：

- **参与者包里没有这批改动。** `repos\Apex` 和便携 zip 停在 08-28 上午那一版
  （`f301231` + 另一个会话修的重复排队缺陷），构建戳上写明了不含这些。
- **我不会自己重打包换上。** 打好的包就是这项研究的仪器，悄悄换掉它正是笔记里已经
  记过一次的那类失败。要不要发是你的决定。
- 代码全在仓库里，随时可发。三条路和推荐见姊妹文档 §12.8。

---

## 9. 进展：§1.2 那个问题有答案了（2026-08-28）

**"这套 LLM 调用相对 `MockCoach` 到底多带来了什么"——量出来了：重合度很低。**
从 08-25 提出到现在一直没量，而它是唯一一个能决定论文里"AI 辅导"这个词能怎么写的
数字。

### 9.1 不用加载模型就能测

散文是模板、数字是 `const`，所以模型的全部自由度就是**挑哪几个 `(弯, 指标)`**。
而每个审计文件里已经存着当时喂给 provider 的那个 evidence packet
（`audit.py:130-148` 的 `evidence_summary`）。所以只要把 `MockCoach` 在**同一个
packet** 上重放一遍，跟当时真的返回的那份报告比选择就行了 —— 不加载 2.1 GB 权重、
不重新推理、不改写任何审计文件。

工具落在 `scripts/coach_overlap.py`，只读，默认读 `APEX_WORKSPACE/sessions`：

```
python scripts/coach_overlap.py [sessions_dir] [--json out.json]
```

比两个层次：每条 finding 的**首条引用**（这张卡到底在说什么），和整份报告的
**引用集合**（Jaccard）。分开比是因为一张卡就是一条建议 —— 两份报告顺带引到同一个
弯，不代表它们在说同一件事。

### 9.2 结果（44 份成功审计 / 15 圈 / 5 个 session）

```
same cards, same order : 1/44
identical prose        : 0/44
lead-citation overlap  : 23/194 = 0.12
mean citation Jaccard  : 0.27
```

**只有 1 份 44 分之一是完全一样的三张卡**，而那一份是因为当时只有一个弯够格出卡
（G1 M1），双方没得选。

### 9.3 但低重合**不等于**"模型判断得更好"

差异是**系统性的，不是逐弯的判断分歧**。看首条引用的指标分布（comparison 模式，
29 份）：

| | 指标分布 |
|---|---|
| Granite | `entry_speed` 23、`throttle_point` 14、`min_speed` 9、`brake_point` 9、`exit_speed` 6，尾巴还有三个 |
| MockCoach | **`min_speed` 62**、`exit_speed` 10、`throttle_point` 6、`brake_point` 4、`full_throttle` 1 |

`MockCoach` 的策略是"按 `time_lost_s` 排弯，取该弯最大的那个机会"，而在这条赛道上
那几乎永远是 `min_speed`——**83 条里 62 条**。它不是在跟模型比判断力，它是一个
几乎只会说一句话的基线。

所以这个 0.12 要这么读：**两套不同的选择策略，不是两个关于同一圈的意见。**
论文里能说的是"LLM 的选择不能由这个确定性基线复现"，**不能**说"LLM 选得更对"——
后者需要一个外部正确答案，而这批数据里没有。

> 真要往"更对"上走，需要的是别的东西：教练评分、或者 §2.C 那条依从度链路
> （被点名的弯后来真的动了没有）。依从度现在已经有了（§6），等样本够了它就是
> 这个问题的自然答案。

### 9.4 它比 mock 克制得多

每份报告出几张卡：

| | 1 张 | 2 张 | 3 张 |
|---|---|---|---|
| Granite（comparison） | 4 | 11 | 14 |
| MockCoach（comparison） | 2 | — | 27 |
| Granite（single_lap） | **10** | 4 | 1 |
| MockCoach（single_lap） | — | — | 15 |

`MAX_FINDINGS = 3` 是上限不是配额，mock 基本每次顶满，模型经常只说一两条。
**这是目前为止唯一一处模型行为明显不同于"排序取前三"的地方**，而且方向是对的
（少说几句好过凑数）。但它没有被记录成任何指标 —— 现在也不知道"只出一张卡"是
模型判断只有一件事值得说，还是它单纯少写了。

### 9.5 单圈模式接近退化 —— F 的事后证据

single_lap 那 15 份里，**14 份的首条引用是 `brake_applications`**，10 份只有一张卡。
也就是说单圈模式下模型的回答接近一个常数。

这正是 §2.F 说的那个后果的实测版：最快圈那一圈掉进单圈模式，只有 4 个 technique
check，于是"参与者最想听解释的一圈"拿到的是几乎每次都一样的一句话。**F 已经把这条
路堵掉了**（最快圈现在对着 composite 读，见 §8.3）—— 这批数字是它当时缺的那个证据，
只是来晚了。

### 9.6 这 44 份**不是**参与者读到的东西

必须写清楚，免得被当成曝光记录用：

- 时间戳是 `2026-08-24T16:26` 到 `2026-08-27T11:07`，**全都在对应 session 结束之后**
  （0823 的圈是 08-23 17:52 跑的，审计是 08-24 写的）；
- 每一圈都同时有 comparison 和 single_lap 两份，这是研究者在本机把每圈两种模式都扫了
  一遍的形状，不是参与者点出来的；
- 参与者实际读了什么，唯一的记录是 §7 的曝光日志，不是这里。

所以这批数字回答的是"**这套流水线在真实圈上会怎么选**"，不是"**参与者被告知了
什么**"。对 §1.2 那个问题来说这就够了，但两者不能互相顶替。

### 9.7 结论

- **"AI 辅导"这个干预不是 `MockCoach` 换层皮**——重合度 0.12，可以这么写进论文。
- 但**别写成"模型选得更准"**：这批数据只证明选择不同，没证明谁对。
- 顺带得到两个可以当质量指标记的东西：**每份报告的卡片数**（模型的克制程度）和
  **单圈模式的指标熵**（现在接近 0）。
- 这个脚本随样本增长可以重跑，人越多结论越硬。

---

## 10. 进展：E 已完成（2026-08-28）

四个 `NOTABLE_*` 常量的注释一直写着"设在一般圈间散布之上"。**现在量过了，这句话对
其中最重要的一个是假的**，而且它造成的不只是"多说几句废话"——它就是 §9 里
`min_speed` 统治一切的原因。

### 10.1 先量：没人量过的那个散布

在 15 圈真实数据上，每个 run 用最快圈当坐标系（`detect_corners` 是位置编号，必须
定一条锚圈，理由同 `adherence` / `reference`），逐弯逐通道算圈间 population sd：

| 通道 | 阈值 | 中位 sd | p75 | 最大 | 散布 > 阈值的弯 |
|---|---|---|---|---|---|
| `min_speed_kmh` | **3.0 km/h** | **8.0 km/h** | 17.2 | 53.1 | **34 / 43** |
| `brake_point_m` | 10.0 m | 8.5 m | 14.7 | 35.4 | 13 / 30 |
| `throttle_reapply_m` | 10.0 m | 7.1 m | 16.5 | 33.0 | 2 / 7 |
| `coast_distance_m` | 15.0 m | 10.3 m | 17.0 | 44.0 | 3 / 7 |

`min_speed` 的阈值比它自己的噪声底**低 2.7 倍**，而且它是唯一一个**每个弯都测得到**
的通道。一个最慢点在弯里飘 8 km/h 的人，某一圈低了 4 km/h，这不是关于他驾驶的事实，
这是"他开了两圈"这个事实。

> 工具：`scripts/`（`corner_scatter` 在 `features.py`，可直接对任何 session 重算）。
> n=3 的 sd 自带约一半的误差，所以它**只用来抬高门槛，从不降低**，见 10.2。

### 10.2 改成什么

一句话：**"值得说"的门槛 = max(固定阈值, 这个人在这个弯自己的散布)**。

- `features.corner_scatter(laps, anchor)` —— 唯一的散布定义，锚圈网格，population sd，
  某圈没测到的通道**缺席而不是记 0**（`study._blank` 的同一立场）。
- `features.notable_bar(metric, corner, scatter)` —— 唯一的门槛定义。
- 三个读者全部改用它：participant 读的 debrief（`debrief._candidates`）、跨弯角
  banner（`features.corner_patterns`）、依从度（`adherence`，它本来就在
  `Shift.required` 上做 `max`，现在两边是同一个 bar）。

**取大**这一步是让 3 圈 sd 能用的全部理由：它只可能抬高门槛，所以 sd 估错**只会漏掉
一个真实观测，永远不会造出一个**。这和 `adherence` 依赖的是同一个不对称性。

还有一个刻意的设计：**门槛记在 `DebriefPoint.threshold` 上，而不是事后重算**。依从度
是几个月后在另一台机器上算的，重算会让它随"当时手边有哪些圈"漂移。理由和 `metric` /
`gap` 记在点上是同一条。

### 10.3 它同时是 §9 那个 `min_speed` 统治的原因

阈值不只是闸门，**它还是把不同单位的通道排到一起的除数**（`score = |gap| / 门槛`）。
除数偏小的通道不是"偶尔混进来"，是**每次都赢**：

```python
# coach.py MockCoach._finding_for
add("braking",   metres / 10.0, ...)   # brake_point
add("cornering", slower / 3.0,  ...)   # min_speed   ← 除数是噪声底的 1/2.7
add("throttle",  metres / 15.0, ...)   # throttle_point
focus = max(FOCUS_AREAS, key=lambda n: opportunities[n]["score"])
```

20 m 的刹车差得 2.0 分，9 km/h 的弯速差得 3.0 分 —— 于是这个弯被报成"速度问题"。
§9 量到的 **mock 83 条里 62 条是 `min_speed`**，不是关于赛道或参与者的事实，是这个
除数的事实。debrief 那一侧同样：73 个点里 **58 个**的解释句来自 `min_speed`。

### 10.4 一个谁也满足不了的要求（这才是真正的 bug）

改之前：**给建议**用固定阈值，**判有没有照做**用 max(固定阈值, 个人散布)。两条不同的
线。后果在 sample session 上一抓一个：

```
T9  min_speed  要求移动 7.70 km/h   算作照做需要 7.83 km/h
```

软件让参与者去找 7.7 km/h，然后规定找到 7.83 才算找到了。**这个要求没有人能满足**，
而每一个这样的弯都会以"被告知了、但没做"的身份进入 manipulation check —— 也就是
§2.C 那条链路最关键的那个数。

现在两条线是同一条，并且有一条守卫测试直接断言它：
`test_what_the_debrief_asks_for_is_never_less_than_what_counts_as_doing_it`。

### 10.5 真实数据上的效果

**debrief（73 个有时间损失的弯，5 个 session）：**

| 解释句来自 | 改前 | 改后 |
|---|---|---|
| `min_speed_kmh` | 58 | 46 |
| `brake_point_m` | 7 | **10** |
| `coast_distance_m` | 0 | **2** |
| `throttle_reapply_m` | 2 | 1 |
| （没有解释句） | 6 | 14 |

**只有 8/73（11%）的点失去了解释句**，界面不会空掉——弯还在、时间损失还在，只是
不再硬安一个通道上去。刹车和滑行拿回了它们本来就该赢的那些弯。

**依从度（真实配对）：**

| 参与者 | | 提出的要求 | 照做 | rate | mean shift_sd |
|---|---|---|---|---|---|
| B0826 baseline→coached | 改前 | 7 | 3 | 0.429 | 0.90 |
| | 改后 | **6** | 3 | **0.500** | 1.01 |
| C0826 baseline→control | 改前/改后 | 6 | 0 | 0.000 | −0.46 |

要看清楚这里发生了什么：**分子没动（3 还是 3），少掉的是分母里那个不可能满足的要求。**
这正是"取大"承诺的方向——它不会制造出依从，只会去掉一个本来就不该计入的问号。
对照组一个数都没变。

> n=1/臂，这不是证据，只是方向对。真正的数要等样本。

### 10.6 没做的：`opportunity_catalog` 那一组阈值

代码里其实有**四组**阈值，第四组在 `coach.py:opportunity_catalog`，管的是**模型能引用
哪些 (弯, 指标)** —— 也就是参与者读到的那几张卡。它没改，而且它跟另外三组本来就
对不齐：

| 指标 | `opportunity_catalog` | `NOTABLE_*` |
|---|---|---|
| `brake_point` | ≤ ref − 10 | 10 ✓ |
| `min_speed` | ≤ ref − 3 | 3 ✓ |
| `throttle_reapply` | ≥ ref + 15 | **10** ✗ |
| `coast_distance` | ≥ ref + 10 | **15** ✗ |

**没一起改的理由是具体的，不是没时间**：`opportunity_catalog` 只拿得到 evidence
packet，而 packet 是**每条审计的匹配键**（`latest_coaching_report` 要求
`evidence_summary ==` 完全相等）。要让它知道散布，就得把散布塞进 packet，那会让
**每一份已存的分析全部失配**——那些文件是"这个参与者到底被告知了什么"的唯一机器可读
记录，也正是 §9 的输入。为一个阈值改动去搅动它，在数据还在收的时候不划算。

所以现在的状态要说清楚：**debrief 那一侧的门槛认这个人自己的散布，卡片那一侧还不认。**
这个不一致以前就在（上表后两行），现在被拉大了。下一步该做的是决定散布该不该进
packet，而 §10.3 已经给了它一个明确的诊断：`min_speed` 的除数 3.0 就是 §9 那个
62/83 的原因。

### 10.7 验证

- **红过再绿**：把 `notable_bar` 改回只认常量，5 个测试立刻红，其中包括 10.4 那条
  守卫；恢复后全绿。
- `795 passed / 17 skipped`（改前 782/17，新增 13 个测试，全在 `tests/test_scatter.py`）。
- `ruff check src tests` 干净。
- 文档里引用的数字（58→46、11%、校准表）是**用发布路径**重跑确认的，不是用一次性脚本
  算完就写上去的。

### 10.8 这批改动同样动了参与者读到的东西

跟 §8.10 一模一样的性质：debrief 的措辞会变、有 11% 的点不再给解释句、banner 的触发
条件变严。**参与者包没动**，代码在树里。它应该和 **G（中文）/ D（假 confidence）**
攒成同一次版本跳，而不是各自单独发。

---

## 11. 进展：G 换掉了做法，并且顺带发现了一个更重的问题（2026-08-28）

### 11.1 G 原来的前提不成立

原写法是"把 15 条模板本地化成中文"，理由是"参与者里若有中文母语者，英文建议是理解力
混淆项"。**这是英文授课的硕士项目**，参与者有语言门槛证明，前提失效。

更要紧的是做了会**新造一个混淆项**：一部分人读中文、一部分读英文，语言就成了两条臂
之间没控制的变量，而问卷（`participant.py`）只记 racing_games / sim_racing /
driving / age_band，**没有语言字段**，事后连控制都控制不了。

但 G 底下那个担心是真的，只是**跟语言无关**：模板是用**事实核查器的语域**写的，不是
用司机的语域写的。原文长这样：

> "The cited brake-onset position is displaced toward the approach."
> "The cited comparison shows a larger gap between brake release and throttle pickup."

`cited` 几乎每句都有——那是校验架构漏进参与者文案里的词，对读的人没有任何意义。而这
批参与者的问卷是：racing games "a few times"、sim racing "casual"、"licence, rarely
drive"、18–24。

### 11.2 然后量到了一件更重的事：他们用的是键盘

这是本次最重要的发现，而且是**测出来的，不是猜的**。

研究自带的 TORCS runtime，`drivers/human/preferences.xml`：

```xml
<attstr name="throttle"    val="Up Arrow"/>
<attstr name="brake"       val="Down Arrow"/>
<attstr name="left steer"  val="Left Arrow"/>
<attstr name="right steer" val="Right Arrow"/>
```

而遥测自己也这么说。按住的键由游戏按**固定斜率**推进，所以转向要么以一个速度动，
要么不动。全部 20 条真实圈（5 个 study run + sample session）：

```
每一圈的转向变化里，落在同一个速率 ±10% 之内的比例
0823 / B0826 / C0826 / sample :  0.941 – 0.953     速率 ≈ 1.235 /s
```

**手不会以一个恒定角速度转十九次里的十九次，只有斜坡会。** 这就是
`f1coach_core/input_device.py` 判定的依据，阈值定在 0.8（远低于实测），而且是单边的：
误判成 analogue 只损失措辞，误判成 keyboard 会跟人讲他没按的键。

> 顺带补一个洞：**设备以前哪里都没记**。capture manifest 钉死赛道、车、圈数，
> 只字不提操控。现在写进审计记录（顶层 `input_device` + 判据），**刻意不放进
> `evidence_summary`**——那个 dict 是审计的匹配键，加字段会让已有分析全部失配（§10.6
> 的同一条规矩）。

### 11.3 15 条里有 14 条是给踏板写的

键盘上刹车从无到有由游戏用固定斜率完成，司机唯一能控制的是**什么时候按下、什么时候
松开、按多久**。而模板在要求：

| 模板 | 要求 | 键盘上 |
|---|---|---|
| `peak_brake` | "Build brake pressure smoothly" | **做不到** |
| `brake_release` | "Release brake pressure progressively" | **做不到** |
| `min_speed` | "Release the brake smoothly" | **做不到** |
| `throttle_reapply` | "Begin squeezing the throttle progressively" | **做不到** |
| `exit_throttle` / `full_throttle` / `throttle_point` | "smooth pedal build" | **做不到** |

只有 `entry_speed` 一条本来就与脚无关。**这不是"难以执行"，是"无法执行"**，而无法执行
的建议和被无视的建议在数据里长得一模一样——正是 §2.C 那条依从度链路要排除的混淆，
只不过这一次它是必然发生而不是可能发生。

### 11.4 改了什么

- **`f1coach_core/guidance.py`** —— 15 条句子的**唯一来源**。以前有两份手工维护的
  拷贝（`llm.py` 的 `_ground_model_prose` 和 `coach.py` 的 `MockCoach`），而且**已经
  漂移了**：§9 量到 44 份里 0 份措辞相同，其中一部分就是这个原因。现在两条路读同一张表。
- **两个语域**：analogue（踏板）与 digital（按键）。digital 那一套**从不提压力**，
  只提按下 / 松开 / 按多久，并且只点名研究实际绑定的 Up / Down Arrow。
- **`f1coach_core/input_device.py`** —— 从这个人自己的转向轨迹读出设备，附带判据
  （`steady_rate_share`、样本数），因为这是事后对别人设备下的推断，研究者应该能复核
  而不是只能相信。
- 语域由 `run_audited_coaching` 从圈上测出来交给 provider，**不经过 evidence packet**。

### 11.5 真实参与者圈上的前后对比

同一条 B0826 baseline 的圈，同一个弯：

```
改前   Minimum corner speed is lower than the reference.
       The cited comparison shows more speed lost through the slowest section.
       Release the brake smoothly and preserve momentum through the slowest section.

改后   You slow down more at the slowest part of this corner than on your quicker run through here.
       The Down Arrow is held past the point where the car has slowed enough.
       Let go of the Down Arrow sooner so the car keeps rolling through the middle.
```

### 11.6 一条刻意没有下的结论：`brake_applications`

原建议是 *"Use one progressive brake application and one controlled release."*
而在键盘上，**短按就是唯一能刹得不满的办法**——叫人只按一次，等于叫他放弃唯一的刹车
调节手段。§9 还量到这条是单圈模式下最常出现的一条（15 份里 14 份的首条引用）。

数据也不支持原来的方向。86 个弯的对照：

| 相对参考圈的刹车次数 | n | 平均丢失时间 |
|---|---|---|
| 更少 | 24 | **+1.90 s** |
| 一样 | 42 | +0.74 s |
| 更多 | 20 | **+0.34 s** |

相关系数只有 −0.081（弱，且是观察性的、有反向因果的可能），**所以不能反过来说"多按更
快"**。能说的是：这批数据里没有任何迹象支持"少按更好"。

因此 digital 版本**不下"只按一次"这个结论**，改成一个两边都成立的要求——把刹车在转向
之前做完，而不是进弯之后再补：

> "Short presses of the Down Arrow are how a keyboard brakes gently, so more than
> one is not wrong in itself. Try to have the braking finished before you turn in,
> rather than adding more once the car is already in the corner."

这一条的措辞是**教练学上的判断**，不是测量结论，需要你确认。

### 11.7 验证

- `815 passed / 17 skipped`（改前 795/17，新增 20 个测试，全在 `tests/test_guidance.py`）。
- `ruff check src tests scripts` 干净。
- 新测试大多是**否定式**的，因为文案层以前一条断言都没有：任何句子不许含数字
  （否则会在参与者等结果的时候被 contract 打回）、不许含旧语域的词、digital 一套不许
  出现压力词、不许点名研究没绑定的键、每个模型可引用的 metric 必须有句子（缺了会让模型
  自己写的散文**原样发布**）。其中两条**当场抓到我自己写错的字**（digital 的
  `peak_brake` 说了 "harder"，`coast_distance` 说了 "pedals"）。

### 11.8 同样进不了当前的包

和 §8 / §10 一样，这改的是参与者读到的东西。**参与者包没动。** G（现在是这个版本）、
D、E 应该攒成同一次版本跳。

---

## 12. 进展：D 已完成（2026-08-28）

### 12.1 它假到什么程度，量出来了

三条路径各假各的：

| 路径 | confidence 从哪来 |
|---|---|
| mock | `round(min(0.9, 0.5 + 0.8 * time_lost), 2)` —— 时间损失的线性函数 |
| LLM | **模型自己填**，然后 `_ground_model_prose` 把 focus / issue / cause / action 全覆盖掉，**唯独把这个数留着** |
| 界面 | `coach_panel` 渲染成 `high · 0.85`，≥0.8 是绿色 |

真正被送到参与者眼前的那 **89 条 finding**（5 个 session 的全部审计）：

```
0.80 ×10   0.85 ×28   0.88 ×1   0.90 ×40   0.95 ×9   0.98 ×1
```

**没有一条低于 0.80。** 也就是说整个研究里的每一张卡片、每一个参与者，看到的都是同一个
绿色的 "high"。它不只是没有依据 —— 它**从不变化**，所以不携带任何信息，却看起来携带。
一个对什么都同样打包票的东西不是在报告可靠性，是在给建议镀一层权威。

### 12.2 为什么不换成"可算的"（原文的选项 a）

原文建议"换成可算的（这个差值是该参与者自己圈间散布的几个 sd）"。**算不出来**，
理由正是 §10.6 那条边界：散布是 session 级的，而 coaching 这条路只拿得到 evidence
packet，要让它知道散布就得改 packet，就会让已存的分析全部失配。

但更根本的理由是：**E 之后已经没有剩余的可靠性梯度需要表达了。** 一条 finding 能被发布，
前提就是它的差值清过了一个**设在这个人自己在这个弯的散布之上**的门槛
（`features.notable_bar`）。所以屏幕上的每一条都已经通过了 confidence 声称要报告的那个
检验；再给一个 0–1 的数，只是把"通过了"重新包装成"有多确定"。

而仍然在 finding 之间变化的两件事 —— **差值多大**、**它花了多少时间** —— 卡片上本来就有，
而且是从圈上量出来的真数。

**所以是删掉，不是换掉。**

### 12.3 改了什么

- **schema 不再问**（`llm.py:build_coach_response_format`）。这一步单独重要：只要 schema
  还要求它，一个强制执行 schema 的服务器就会**逼**模型每次都编一个，而它进不进审计文件
  就只取决于下游有没有一行代码记得丢掉它。
- **grounding 里显式丢弃**（`_ground_model_prose` 里 `finding.pop("confidence", None)`），
  和覆盖散文同一个位置、同一个理由。
- **mock 不再算**。
- **两处渲染删掉**：`coach_panel` 的 chip、`report.py` 导出 HTML 里的 chip。
- **`Finding.confidence` 变成 `float | None = None`**，`to_dict` 不再写出。

### 12.4 为什么契约里还留着这个字段

因为**直接删会让 30 份已存的参与者分析全部读不出来**。`latest_coaching_report` 会用当前
的严格契约重新校验存档（`set(raw) == required_finding`），而每一份旧记录都带着这个数。

而那些文件是"**这个参与者当时到底被告知了什么**"的唯一记录。拒绝它们不会把那个数消掉，
只会把"它曾经被展示过"这件事一起藏掉。

所以契约从"必须恰好是这几个键"放宽成"必须包含这几个键，另外容忍 confidence"：
**旧记录照常载入并能显示，新的 finding 一个都不写。** 载入的旧值留在对象上，但
`to_dict` 不会再把它写出去，界面也不会再画它（有一条测试专门锁这两件事）。

### 12.5 验证

- **30 份真实参与者分析全部恢复成功，0 份失配。** E、G、D 三轮下来 evidence packet
  一个字节没动，所以没有任何一份存档被孤立 —— 这是每一轮都刻意守住的那条线（§10.6、
  §11.4）。
- `819 passed / 17 skipped`（改前 815/17）。`ruff check src tests scripts` 干净。
- **红过再绿**：把 chip 加回卡片，`test_a_card_makes_no_claim_about_how_sure_anything_is`
  立刻红（`Braking high · 0.95 …`），去掉后绿。
- 新测试锁住的是：schema 里没有这个词、模型给的三个不同的数一个都不留、mock 不写、
  旧记录能载入但不再写出、卡片上既没有数也没有 "high"。

### 12.6 同样进不了当前的包

和 §8 / §10 / §11 一样。**参与者包没动。** B/F、E、G、D 四批攒成同一次版本跳。

---

## 13. 发包记录（2026-08-28 ~16:20）

**这是第一个改变参与者读到的内容的构建。** 前三位参与者（0823、B0826、C0826）读的是
旧措辞，从这一版起两批可以靠 `build` 列分开：他们的曝光日志里那一列是空的，之后写的
每一条都是 `d37e916+`。

### 13.1 发包前的三个前提，都做了

| 前提 | 落地 | 守卫测试 |
|---|---|---|
| 构建标识进曝光记录 | `f1coach_core/build.py` + `apex.spec` 把 commit 写进 bundle；`ExposureLog.opened` 盖章，**不问调用方**（会忘） | `test_the_build_that_showed_the_advice_is_on_every_recorded_view` |
| banner 计入 `advice` | `analysis_view._start_report_view` 现在是 `count > 0 or banners > 0` | `test_a_report_that_is_only_a_habit_banner_is_recorded_as_coaching_read`（去掉修复立刻红） |
| banner 单独计数 | `ReviewView.patterns`，导出多一列；旧日志里是**空白不是 0** | `test_a_banner_is_counted_apart_from_the_findings_it_sits_above` |

`advice_seconds` 的含义没有变（它只加 corner view），这一点是**断言过的**，不是假设的
—— 那一列是剂量-反应的自变量，悄悄变宽会让已经导出的每一个剂量改变含义。

### 13.2 交换过程

`827 passed / 17 skipped`、ruff 干净之后，构建到 D:（profile 卷只剩 4.2 GB，见记忆
里那条），换上两个 exe，`_internal` 只补进新增的 `build_id.txt`。

**验证过的，不是假设的：**

- 用 PyInstaller 自己的 archive reader 把 `PYZ.pyz` 取出来 unmarshal：装上的两个 exe
  里 `f1coach_core.guidance` / `input_device` / `build` 三个新模块都在，
  **两个备份里一个都没有**。
- `racecoach.exe --help` 退出码 0；`Apex.exe` 从包目录启动，11.5 秒出现标题 "Apex" 的
  窗口，干净退出（用的是 D: 上的临时 `APEX_WORKSPACE`，没碰参与者数据）。
- `racecoach.exe study-exposure` 导出的表头里有新的 `patterns` 和 `build` 两列并且
  带出了值 —— **新的记录形状在打包后的二进制里是通的**，不只是在源码里。

### 13.3 一个差点毁掉包的东西

`_internal` **没有** mirror。先逐文件比过：新构建 1073 个文件，包里 1074 个，没有大小
差异，新构建多一个 `build_id.txt`，而**包里多两个**：`libcrypto-3-x64.dll` 和
`libssl-3-x64.dll`，6.8 MB 的 OpenSSL。`_ssl.pyd` 两边都在而且字节相同，所以那两个
DLL 就是它背后的全部 —— `/MIR` 会把它们删掉，`import ssl` 从此坏掉，而下载模型走的
就是它。

> 已知，且下次装便携包时要注意：`dist\Apex` 本身缺这两个 DLL。从装好的包里拷过去，
> 或者在组装好的副本里先验一下 `import ssl`。（已写进记忆。）

### 13.4 一件不影响使用、但影响可复现性的事

构建标识是 **`d37e916+`**。`+` 表示"这个 commit 加上没提交的改动"，而这次没提交的是
24 个文件。它足够把两批参与者分开（那是前提要求的），但**不能反查回代码**。

`apex-study-build.txt` 里记了新增的三个模块、改动的文件清单和两个 exe 的 SHA-256，
那是目前的重建凭据。如果要让这个标识永久可解析：提交之后重新构建再换一次（约 3 分钟），
标识就会变成一个干净的 commit。这件事**在有下一位参与者跑之前做都还来得及**。

> **已做。** 见 §14 —— 2026-08-29 09:57 换成了干净标识 `5698381`。上面这一段保留原样，
> 因为它是当时的判断记录；但**不要再按它行动**。

## 14. 重新发包：让构建标识可解析（2026-08-29 ~09:57）

§13.4 留的那件事做掉了。24 个文件在 `d866839` 里提交（"Add guidance and input device
handling for participant telemetry"），工作树里只剩一个 ruff I001 的导入排序没落地，
一起提交为 `5698381`，然后从**干净的树**重新构建、重新交换。

现在的构建标识是 **`5698381`**，没有 `+`，可以直接 `git checkout` 回去。

### 14.1 打包的代码一个字节都没变

这是这次交换里唯一真正需要证明的事 —— 研究进行到一半换掉仪器，必须能说清参与者读到的
东西有没有变。

把**被换下的** exe 和**装上的** exe 的 `PYZ.pyz` 都用 PyInstaller 自己的 reader 取出来，
按模块逐个比对 marshal 后的字节：

| | 应用模块数 | 只在旧的 | 只在新的 | 字节不同 |
|---|---|---|---|---|
| `Apex.exe` | 86 | 0 | 0 | **0** |
| `racecoach.exe` | 59 | 0 | 0 | **0** |

两个构建之间唯一的差别是 `build_id.txt` 里的 8 个字符。

**所以做分析时 `d37e916+` 和 `5698381` 是同一个条件，不是两个。** 这次交换没有改变任何
参与者读到的内容。`apex-study-build.txt` 里现在有一张标识对照表，写清了这一点。

### 14.2 验证过的，不是假设的

- **干净树**：打包时 `git status --porcelain` 是空的 —— 这正是标识可解析的原因；
  `apex.spec` 打印 `apex.spec: build id 5698381`，没有 `+`。
- 打包前在**这两个 exe 所构建自的那个 commit 上**跑了全量：`827 passed / 17 skipped`，
  ruff 干净。
- 三个新模块在两个新 exe 的 PYZ 里都在。
- **冻结后的应用确实读得到自己 bundle 里的数据。** 对着 D: 上一个空的临时
  `APEX_WORKSPACE` 启动，它把 5 个 sample session 的 CSV 落了盘 —— 那就是
  `files("f1coach_core")/"data"/...` 在 bundle 里解析成功，而 `app_build()` 读标识用的
  是**同一个调用，同一个目录，只差一个文件名**。这是这次能拿到的、对标识读取路径最直接的
  证据（`app_build()` 只在 `ExposureLog.opened` 里被调用，而那只有 GUI 打开报告视图时
  才会走到，没法从命令行触发）。
- `Apex.exe` 8.8 秒出窗口，干净退出，退出码 0；`racecoach.exe --help` 退出码 0；
  `racecoach.exe study-exposure` 导出的表头带 `patterns` 和 `build`。
- `_internal` **没有** mirror，只替换了一个文件：`f1coach_core/data/build_id.txt`。
  先比过文件清单：新构建和包里**完全一致**，各 1075 个，只有 `build_id.txt` 内容不同。
- 临时 workspace 用完删了。整个交换过程没有读写任何参与者数据。

### 14.3 §13.3 的 OpenSSL 结论要改口一半

**这次**构建的 analysis **带上了** `libcrypto-3-x64.dll` 和 `libssl-3-x64.dll`，
所以新构建的 `_internal` 和包里的文件清单一模一样。昨天那次没带上。

同一台机器、同一个 venv，两次构建的二进制收集结果不同 —— 说明这两个 DLL 在不在
`dist\Apex` 里**不是稳定的**。结论因此从"`dist\Apex` 缺这两个 DLL"改成更弱、也更安全的
一条：**装便携包之前先确认这两个 DLL 在，或者直接验 `import ssl`**，别假设它们一定在，
也别假设它们一定不在。

### 14.4 还剩下的

和 §10.6、§11.6 一样没变：`opportunity_catalog` 那组阈值仍未标定，
`brake_applications` 的数字键措辞仍是一条**教练判断**而不是测量结论，等你确认。

## 15. 把天花板抬起来（2026-08-31）

> 起点是队友仓库那套 AI 教练能不能接进来。读完两边的码，结论是**不接它的模型，移植它的
> 三个机制**：队友的 coach 是 DeepSeek 云 API（`server.mjs:65`），接过来意味着参与者
> 遥测出境，外加一个云依赖成为实验仪器的单点故障。值得拿的是它**校验数字**而不是禁止
> 数字的做法、那套"说人话不说字段名"的措辞规则，以及跨圈的不重复。
>
> 另外，Apex 的采集尚未开始，所以 §10.6 当时"数据还在收，不划算"的理由也不再成立。

### 15.1 §10.6 那条阻塞其实不用付它的代价

§10.6 说要让 `opportunity_catalog` 认散布，就得把散布塞进 evidence packet，而 packet 是
每份审计的匹配键，动它会让已存的分析全部失配。

**不用。散布作为参数传进去就行，packet 一个字节都不动。** 关键性质是单调的：
`bar = max(fixed, scatter) >= fixed`，所以认散布的目录永远是原目录的**子集**。
生成时用严格的门槛，审计时不带散布用宽松的门槛照样接受同一份报告。
这条性质有测试盯着（`test_a_card_chosen_against_the_spread_still_stands_without_it`）。

代码里本来就有这个模式的先例：`CoachProvider.device` 的注释写得很清楚——
"不属于 evidence packet，因为那个 dict 是每份审计的匹配键"。散布走同一条路，
由 `run_audited_coaching` 设置，因为那一层握着 lap。

### 15.2 卡片这一侧现在和 debrief 用同一把尺

四组阈值里的第四组（`opportunity_catalog`）不再自带常量，改成读 `NOTABLE_THRESHOLDS`：

| 指标 | 改前 | 改后 |
|---|---|---|
| `brake_point` | ref − 10 | 10（本来就一致） |
| `min_speed` | ref − 3 | 3，**且认散布** |
| `throttle_reapply` | ref + 15 | **10** |
| `coast_distance` | ref + 10 | **15** |

`NOTABLE_THRESHOLDS` 没有标定的五个指标（`entry_speed`、`exit_speed`、`throttle_point`、
`full_throttle`、`exit_throttle`）保留原常量，集中在 `_UNCALIBRATED_BARS`，不再散在 lambda 里。

**在 sample session 上量到的效果**（`laps[0]` 对 `laps[4]`）：卡片从 **20 张降到 17 张**，
掉的三张**全部是 `min_speed`**（T1、T5、T8）。这正是 §10.3 的诊断——`min_speed` 的 3.0 km/h
门槛低于这位车手自己 8 km/h 的抖动，所以它既不可能不响，又因为门槛同时是排序的除数而必然获胜。

### 15.3 模板从"无条件覆盖"变成"兜底"

这是这批改动里唯一改变参与者读到的**文字来源**的一条。

改前：`_ground_model_prose` 在校验**之前**无条件跑，把 focus/issue/cause/action 全部换成模板。
配合 `_require_measurement_free_prose`（prose 里出现任何数字直接判失败），
结果就是 §1 那个结论——**模型一个字都没写到参与者眼前**，它唯一的自由度是挑哪几个 (弯, 指标)。

改后：

- 先用模型自己的措辞过一遍完整合同。过了就**保留模型的话**。
- 没过才落到模板，再校验一次。**兜底路径和改前完全一样**，所以这条改动的下界就是现状：
  最坏情况下参与者读到的和今天一模一样，不可能更差。
- 数字校验从"一个都不许有"换成**"只能说这条 finding 自己引用的数"**：
  value、ref、两者之差、span，以及弯名里的数字（`T3` 的 3 不是测量值）。
  按模型自己选的精度比较，所以 7.63 写成 `7.63`、`7.6`、`8` 都算同一个主张，
  写成别的数就是别的数——见 `supported_measurements` 和 `_quotes_a_measurement`。

**差值是特意放进白名单的**：它才是这张卡真正在说的量（"这里慢了三公里"），
而且它由两个已经对过遥测的数推出来，不是在旁边现编的。

一个副作用要说清楚：**同一份报告里现在可能混着模型的话和模板的话**。改前所有 finding 都被
压成模板，措辞相同的会合并；现在一条过了校验、一条落到模板，它们说的确实不是同一句话，
就不再合并（`test_duplicate_mislabelled_comparison_finding_is_grounded_and_merged` 改成盯这件事）。

### 15.4 措辞规则和跨圈不重复

提示词（`PROMPT_VERSION` `coach-v4` → **`coach-v5`**）现在明说：可以写数字，但只能写这条
finding 引用的；用车手的话说这个数，不要说字段名（"中段慢了三公里"，不是 "min_speed 46.2"）；
**不要打印负数**，说差在哪一侧；每句最多一个数；不要给 packet 里没有的目标（刹车距离、
时间收益、百分比）。后面四条是从队友的 `coachSystemPrompt` 搬来的。

`prior_advice()`（`audit.py`）读同一个 session 里**其他圈**的审计记录，取每条 finding 的
`action`，最多 6 条，塞进提示词并要求不要重复。只取 action：要求车手做的事不该重复，
而同一个弯的 issue/cause 再说一次是合理的。

**为什么这不破坏可复现性**：审计记录本来就逐字存 `"prompt"`，所以当时喂给模型的
"已经说过的话"是留档的。而且它按圈过滤掉自己（`test_a_lap_is_never_told_what_it_was_itself_told`），
否则重新生成会被要求"不要重复你自己"。

队列那边把 laps 交给 job，散布在 **`run()` 里**算而不是构造函数里——构造函数跑在 UI 线程上、
每圈一次；实测一次 `corner_scatter` 在 5 圈的 session 上是 27 ms，20 圈的场次总量约 2 秒，
相对每圈几分钟的模型调用可以忽略，但放在 UI 线程上就会卡。

### 15.5 验证

- **红过再绿**：把模板改回无条件覆盖，`test_the_model_keeps_its_own_words_when_they_quote_its_own_evidence`
  和那条合并测试立刻红；恢复后全绿。
- 白名单第一版按字符串枚举写，**被自己的测试抓到一个真 bug**：`1595.0` 是被引用的精确值
  却被拒（枚举漏了尾随零）。改成按模型选的精度做数值比较。
- **`844 passed / 17 skipped`**（改前 827/17，新增 17 个测试）。`ruff check src tests` 干净。
- 20→17、掉的三张全是 `min_speed`，是用发布路径跑出来的，不是估的。

### 15.6 拿真 Granite 跑过了：结论是坏消息，而且要你定

上一版这里写"没验"。现在验了，办法是绕开参与者目录：权重本来就能自己下载，按 `granite/model.py`
里钉死的 URL 和 SHA-256 拉了一份到 `D:\granite-scratch\`，摘要与钉死值逐字节相同
（`662b0626...32eb29`），所以跑的就是参与者跑的那份权重，一个字节都不差。llama-server 用的是
`repos\Apex\granite-runtime\llama-server.exe`（只读，没碰参与者工作区），工作区指向 D 盘的临时目录。

**第一件事：模型的措辞一次都没上过屏。** sample_session 五圈，发布 8 条 finding，全部落模板。
每圈 80-160 秒，无一失败。也就是说这批改动对参与者读到的字，**实际影响为零**——真正起作用的只有
§15.2 的阈值标定和 §15.4 的不重复。

**第二件事，比第一件严重：白名单管的是"数字有没有被引用"，不是"这句话对不对"。**
第二轮采样里唯一一条三个字段全干净、按新代码会原样上屏的 finding 是这样的：

```
引用证据  brake_point @ T8   value 1775.0 m   ref 1795.0 m
模型写的  "T8 braking point is later than the reference, indicating insufficient braking effort."
模型建议  "Accelerate the brake application earlier on the approach..."
模板会写  "You start braking earlier than on your quicker run through here."
```

1775 < 1795，这位驾驶员比自己的快圈**更早**踩刹车。模型说成"更晚"，据此判断"刹车力度不足"，
然后建议**再早一点刹**——把本来就过早的动作往错的方向再推一步。三个字段引的数字全部正确，
全部在它自己引用的证据里，所以白名单一个字都拦不住。它拦得住编造的数字，拦不住讲反的因果。

顺带量到的还有：模型频繁**写错弯角**——引用 T1 却在讲 T7，引用 T10 却在讲 T2，五圈里四条这样。
这些反而被拦下来了，因为弯角名里的数字算在白名单内（`T7` 贡献 7），引 T1 就不许说 7。
这是白名单意外接住的一类真错误，值得记一笔。

逐字段看：21 个字段里 9 个数字干净，其中 `action` 一项 7 条里有 6 条干净。所以"按字段回退"
（只把违规的那句换成模板）能救回不少字——但上面那条反向的 finding 说明，救回来的字不一定是对的。

**所以要你定的是这个：**

- **(a) 只发安全的那部分。** 阈值标定、不重复、白名单三条留下（白名单作为比"禁所有数字"更严的
  闸门，纯赚），把"模型措辞可以上屏"这一条撤掉，回到永远用模板。按实测，参与者读到的字和今天
  完全一致，而卡片选择变准了。风险为零，收益是 §15.2/§15.4。
- **(b) 先加方向校验再说。** 让措辞必须和它引用的差值同号才能上屏。可做，但要靠关键词判"早/晚、
  多/少"，本身就脆；而且按第一件事的实测，做完大概率还是全落模板——为一个观测到 0 次生效的路径
  加一层脆弱的检查。
- **(c) 按现状打包。** 实测五圈没有一条模型措辞上屏，下界成立；但第二轮那条说明"上屏"是可能发生的，
  一旦发生就是给参与者看反向建议。这是研究仪器，我不建议。

我的建议是 **(a)**。这批改动值钱的部分从来不是让 3B 模型自己说话，是让卡片挑得准、不重复。
