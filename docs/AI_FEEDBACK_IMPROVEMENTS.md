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

### 1.2 一个应该量化、但还没量化的问题

既然散文是模板、数字是 `const`，那这套 LLM 调用相对 `MockCoach`
（`coach.py:MockCoach`，它同样从真实 evidence 里挑最差的弯）到底多带来了什么？

对同一批 session 跑两边、比 finding 重合度就知道。**如果重合度很高，"AI 辅导"这个
干预在论文里的措辞就要小心。** 这不是坏消息，是必须先知道的事。

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

> 成本：一天。在 `features.py` 加一个 session 级聚合，`coach.py` 的 contract 加一
> 类 `scope: "session"` 的 finding。

### C. 没有"上一条建议有没有落地"

整个研究在测提升，但比较永远是 lap vs session best
（`coaching_queue.py:173-185`），从来不是 lap N vs lap N−1。参与者永远不会被告知
"上一圈说你 T3 刹早 12 m，这一圈是 4 m"。

这一条同时是**研究数据** —— 建议依从度（adherence），正是
[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md) §4.5 那条
剂量-反应缺的另一半。原料 `_corner_facts` 全都有。

> 成本：一天。

### D. confidence 是假的

- mock：`round(min(0.9, 0.5 + 0.8 * time_lost), 2)`（`coach.py:603`）——
  时间损失的线性函数，不是置信度。
- LLM 路径：模型自己填一个 0–1，然后散文被覆盖、**confidence 留着**。
- 界面把它渲染成绿色的 `high · 0.85` chip（`coach_panel.py:276`）。

**这是一个关于可靠性的断言，背后什么都没有。** 换成可算的（这个差值是该参与者
自己圈间散布的几个 sd），或者直接删掉。留着假的最差。

> 成本：半天。

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

> 成本：一天（含把阈值从常量改成"常量与个人散布取大"）。

### F. 参考圈选法太朴素，单圈模式几乎无话可说

参考圈永远是 session best（`coaching_queue.py:173`）。3 圈里的最快圈可能只是运气，
而且它自己也有烂弯。

后果最重的是**单圈模式**：最快圈那一圈是参与者最想听解释的一圈，却只有 4 个
technique check（`features.py:342` 的 `SINGLE_LAP_GUIDES`：coast / brake 次数 /
throttle 次数 / pedal overlap），完全没有弯速相关的建议。

合成参考圈（每个弯取该 session 最好的一次）能同时救这两件事。

> 成本：一天。

### G. 语言 —— 清单里 ROI 最高的一条

全英文。而既然散文就是 15 条固定字符串（`llm.py:340-437`），**本地化成中文是改 15
个字符串，零幻觉风险，因为根本不经过模型**。

如果参与者里有中文母语者，现在的英文建议是一个理解力混淆项：读不懂建议和不采纳
建议在数据里长得一模一样。

> 成本：半天。注意 `_require_measurement_free_prose` 用 `character.isdigit()`
> 判断，中文全角数字（０-９）也会被 `isdigit()` 判为真，本地化时不要在措辞里写数字。

### H. 曝光量仍然没记录

审计文件（`audit.py:write_coaching_audit`）记了 provider / model / prompt / 原始
回答 / 验证过的报告，但它记的是 **lap 文件名**，不记 participant / phase，也没有
任何"参与者实际看了什么"的记录。这是
[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md) §4.5 提的，
至今没做。

> 成本：1–2 天。

### I. 延迟与资源

- `MANAGED_TIMEOUT_S = 900.0`（`coaching_queue.py:29`），CPU 上 3B，一次一个请求。
- `NarrationTask` 每次用完 `server.stop()`（`coach_task.py`），下次再花几十秒
  重新加载 2.1 GB。会话级 debrief 要连着问好几次，这个起停开销会被放大。

> 会话级 debrief 已经改成复用主窗口那台常驻 server（见 §5.3），不再自己起停。

### 另：一个仍未修的 bug

`study_view.py:365` 仍然是 `summary_csv(self._summaries)`，没传 `backgrounds=`
—— 界面上看得见 Background 那一列，导出的 CSV 里那八列全是空的。这是
[STUDY_CAN_WE_SHOW_IMPROVEMENT.md](STUDY_CAN_WE_SHOW_IMPROVEMENT.md) §4.4 / §6.1，
一行修复，至今没修。

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
| 2 | **C** 跨圈依从度 | 同时是给参与者的反馈和给研究的数据 |
| 3 | **G** 中文模板 | 改 15 个字符串，零风险 |
| 4 | **D** 修掉假 confidence | 它现在是一个没有依据的可靠性断言 |
| 5 | **B / F** 模式聚合与合成参考圈 | 反馈质量的天花板在这里 |
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
