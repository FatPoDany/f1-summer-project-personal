# 我们的软件能否证明"接受了 AI 赛后辅导的人开得更好了"？

> 起点是 2026-08-25 的问题：**目前这套软件能不能拿来对比"接受了 AI 赛后反馈辅导的玩家"和"他最开始跑的那几圈"，判断是否有显著提升？**
>
> 下面每一条结论都指向具体代码位置，不凭印象。第 1 节是结论，第 2 节列已经具备的能力，第 3 节讲软件刻意不做的那一件事，第 4 节是真正挡在"显著"前面的六个问题（其中 4.4 是一个实打实的 bug），第 5 节给一条现在就能走的路径，第 6 节是按性价比排序的改动建议。第 8–10 节是逐次的进展记录：§8 = 6.2，§9 = 6.3，**§10 = 6.1 + 6.4，以及第一批真实数据暴露出来的三处问题**。

---

## 1. 结论

**分成三句话：**

1. **采集与测量：胜任。** 条件被冻结、身份跟着文件走、指标不止圈速、缺失通道报成空白而不是 0。这一层是这个项目做得最扎实的部分。
2. **统计检验：软件故意不做，要你在 R / SPSS / Python 里按最后一步。** 这是写进 docstring 并且有测试锁住的立场，不是遗漏。
3. **因果结论"是 AI 辅导带来的提升"：目前的实验设计支撑不了。** 缺的不是代码，是**对照**。现在 baseline → coached 是同一个人在同一条赛道上又跑了一遍，"变快了"至少有三个互相缠在一起的解释：辅导起了作用、单纯多练了几圈、对 TORCS 的操控更熟了。软件可以照样跑出一个漂亮的 p 值，但那个 p 值回答的是"第二组圈比第一组快吗"，不是"辅导有用吗"。

所以对原问题最诚实的回答是：**"是否有提升"能测、能导出、能检验；"提升是不是辅导带来的"现在测不了 —— 这一步要么补一个对照组，要么在论文里把它明确写成 limitation。**

---

## 2. 已经具备的能力

### 2.1 条件被冻住了，所以两组圈之间只差辅导

`default_study_preset`（`src/racecoach/telemetry/human_capture.py:227-245`）把每个参与者要跑的东西钉死：

| 项 | 值 | 为什么钉死 |
|---|---|---|
| 赛道 | `aalborg`（road） | 注释写得很清楚：原来用 CG Speedway 1，run-off 太宽，参与者冲出赛道边缘 14 m 都收不到任何伤害，`damage_events` 因此完全没有区分力。换成窄赛道、护栏更近，错误才会 register 成一个数 |
| 车 | `car7-trb1` | 同上，条件一致 |
| 圈数 | 3 | |
| 窗口 | 1280×720 | 冻进 manifest，因为"不同的渲染尺寸就是不同的条件" |

这一点很重要：**如果画面大小、赛道、车在两个 phase 之间会变，后面任何统计都白做。** 现在不会。

### 2.2 身份跟着文件走，而不是靠一张旁边的表

每个 lap CSV 的头部自带 `# driver:` / `# phase:` / `# setup:`（写在 `src/f1coach_core/torcs.py:156-180`，读回在 `loader.py:99`）。这意味着：

- 数据拷到别的机器、混进别人的池子里，仍然说得清是谁在哪个条件下跑的；
- `summarise_all`（`study.py:177-198`）遇到没有身份的圈**直接丢弃而不是并进一个匿名组** —— 一个归不了组的圈不能站在比较的任何一边。

### 2.3 起步那一圈不会污染平均

一次 3 圈的比赛在文件里是"发车段 + 若干飞驰圈 + 末段"。`split_torcs_run`（`torcs.py:98-141`）在 `dist` 回零处切开，只有**起点在线上**（`starts_at_line`，`:109`）**且被下一次过线关闭**的段才算 `complete`；发车段起点在发车格上，因此被丢掉。

如果不丢，静止起步那一圈会系统性地把 baseline 的 `mean_lap_s` 拉慢，"辅导后变快"就会凭空多出一截。

### 2.4 指标不止圈速，而且这是刻意的

`study.py` 开头那段 docstring 说得最准：一个每圈都跑到界外的人变快了不叫进步，一个不再出界的人即使表上没动也叫进步。所以每个 phase 记录：

| 列 | 来源 | 含义 |
|---|---|---|
| `best_lap_s` / `mean_lap_s` / `sd_lap_s` | `summarise`，`study.py:147-172` | SD 用的是 **population sd（`pstdev`）**，因为这几圈就是这个 phase 的全部，不是从更大集合里抽的样本。它同时是一个"一致性"指标 —— 辅导有没有让人开得更稳，本身就是一个可检验的假设 |
| `off_track_events` / `off_track_seconds` | `_off_track`，`study.py:95-125` | 按**事件**计数而不是按采样比例：出界一次两秒和摇摇晃晃压线十次是两种不同的问题。少于 `MIN_EXCURSION_SAMPLES = 5` 个采样（50 Hz 下 0.1 s）不算 |
| `damage_events` | `_damage`，`study.py:129-145` | 只算**本圈内的上升沿**，因为 damage 在一场比赛里是累计的，第三圈开头就是第二圈结尾 |

**缺失通道报成空白，不报成 0**（`_blank`，`study.py:78-80`）。这一条对统计的意义比它看起来大：把没记录到的通道当成"没有出界"，等于悄悄奖励了那些数据不全的参与者。

### 2.5 配对结构和导出

- `StudyView._paired()`（`src/apex/study_view.py:167-176`）挑出**同时有 baseline 和 coached 的人** —— 配对检验要的正是这个。
- 表头一行话直接告诉你现状：`n of N 有两个 phase；best lap 平均变化 +x.xx s；N 人中有 m 人变快。Export the rows to test whether that is more than chance.`
- 导出：GUI 的 `Export CSV…`，或命令行

  ```bash
  racecoach study-summary --out summary.csv            # 整个 workspace
  racecoach study-summary <汇总目录> --out summary.csv  # 参与者交回来的一堆 session
  ```

  一行 = 一个参与者 × 一个 phase，`PERFORMANCE_COLUMNS`（`study.py:200-210`）九列 + 八列背景。**这就是可以直接喂给配对检验的文件。**

### 2.6 先验经验（组间可比性）

`participant.py` 在参与者第一次开车前问四个**有序分档**的问题：racing_games（5 档）、sim_racing（3 档）、driving（3 档）、age_band（4 档）。设计上刻意粗糙：

- 分档而不是自由文本或精确年龄 —— 够用来检验两组是否平衡，又不足以重新识别任何人；
- 每一项都可以不答，**"没答"和"答了中间值"是两回事**，不会被填成中位数；
- 导出时**标签和 rank 并排给**（`background_columns`，`participant.py:158-170`）：标签给人读，rank 给检验用。

---

## 3. 软件刻意不做的一件事：判定显著

`StudyView` 的 docstring 明写：

> It measures and shows; it does not test. Declaring significance is the analyst's call in their own tool, and a p-value computed quietly by a viewer would be worth less than one they can defend.

而且这条立场是**有测试锁住的** —— `test_the_view_reports_measurements_and_leaves_significance_to_the_analyst`（`tests/test_study_view.py:115-127`）断言界面文字里不出现 "significant"、"p ="、"p<"。

**所以对"能否判断显著提升"的直接回答是：软件把你送到检验的门口，最后那一按是你自己在 R/SPSS/Python 里做。** 这是一个可以辩护的选择（一个界面偷偷算出来的 p 值，评审问起来你答不上它用了什么检验、单双尾、怎么处理缺失），但要知道它就是这么设计的 —— 不要指望打开 Study 页就看到 "p = 0.03"。

---

## 4. 真正挡在"显著提升"前面的六件事

### 4.1 顺序混淆 —— 最致命，而且不是代码问题

现在的流程是同一个人先 baseline 后 coached。任何改善都同时被三件事解释：

1. AI 辅导真的改变了驾驶；
2. **单纯多跑了三圈**（在赛车这种运动技能上，前十圈的学习曲线极陡）；
3. 对 TORCS 键盘/手柄操控本身更熟了。

`familiarisation — not measured` 这个 phase 只能削弱第 3 条，**削弱不了第 2 条**。配对 t 检验在这个设计下能给出一个很小的 p 值，但它检验的命题是"第二组圈快于第一组圈"，而这个命题**在没有辅导的情况下大概率也成立**。

> 这不是软件的缺陷，是设计的缺陷；但它决定了这套软件现在能不能"胜任"你问的那个问题。**能测出提升，不能归因于辅导。**

**两条出路：**

| 出路 | 做法 | 代价 |
|---|---|---|
| **A. 加对照组（推荐）** | 一半参与者在两个 block 之间拿到 AI 辅导，另一半拿到无针对性的通用文字（或什么都不给），两组跑同样多的圈。检验的不是"前后差"，而是**组 × 时间的交互**（mixed ANOVA / difference-in-differences） | 参与者人数翻倍 |
| **B. 承认并写进 limitation** | 只做被试内前后比较，在论文里明确写"改善与练习效应不可分离" | 免费，但结论强度大幅下降 |

如果时间/人手只够 B，那至少要做一件事：**在 baseline 里多跑几圈**，用 baseline 内部的圈间趋势估计一下"纯练习"的斜率，再看 coached 的提升有没有超出这条外推线。这需要 4.3 的逐圈导出。

### 4.2 Study 界面只认两个 phase，对照组进不来

- `_paired()` 里 `BASELINE`/`COACHED` 是**硬编码常量**（`study_view.py:37-38, 175`）。第三个条件的参与者会出现在表里，但**永远配不上对**，headline 还会说 "A paired comparison needs the same participant in both 'baseline' and 'coached'"。
- ~~GUI 采集页的下拉框只有三项，没有 control。~~ **已修（2026-08-25，见 §8）** —— 现在是四项，`Control — second run, own practice only, no AI advice`。
- **但底层不限制**：`racecoach capture-human --phase` 的帮助文字就写着 "study phase/condition slug"（`cli.py:199-202`），只做 slug 合法性校验（`human_capture.py:209`）；`summarise_all` 按任意字符串分组。所以

  ```bash
  racecoach capture-human --participant-id P014 --phase control
  ```

  是可以的，`racecoach study-summary` 也会照常导出这一组 —— **只有 Apex 的 Study 界面不认。**

结论（2026-08-25 已全部解决）：采集那一半由 §8 修掉，展示这一半由 **§9** 修掉 —— Study 界面现在让你自己选比哪两个条件，`baseline` vs `control` 是一个正常选项。本节留作记录。

### 4.3 导出粒度只到 phase 级

一人一 phase 一行，能做配对 t / Wilcoxon，**做不了**：

- **学习曲线**（第几圈 × 是否辅导）—— 而这正是把练习效应从辅导效应里剥出来的手段；
- **混合效应模型**（lap 嵌套在 participant 里）—— 在 3 圈 × n 人这种小样本下，这是最能榨出统计功效的做法，因为它用的是所有圈而不是每人两个汇总数；
- **把先验经验当协变量的回归** —— 背景列有，但只挂在 phase 级的行上。

原始 lap CSV 全都在盘上（`~/Apex/sessions/<name>/*.csv`，每个文件自带 driver/phase），所以**这些分析都做得了，只是软件现在不给那个文件**，要自己写一段脚本遍历 session 目录。

### 4.4 GUI 导出把背景列丢了 —— 这是一个 bug（**2026-08-26 已修，见 §10.4**）

```python
# src/apex/study_view.py:239
Path(target).write_text(summary_csv(self._summaries), encoding="utf-8")
#                                   ↑ 没有 backgrounds=
```

对比命令行那条路：

```python
# src/racecoach/cli.py:423
text = summary_csv(summaries, backgrounds=backgrounds)
```

`summary_csv` 的 `backgrounds` 默认 `None` → `background_columns(None)` → 八个背景列**全部写成空字符串**。

后果：**屏幕上的 Background 那一列看得见，点 `Export CSV…` 导出来的文件里那八列全是空的。** 而 `study.py:226-232` 的 docstring 恰恰写着"分析者不该为了问这个问题去 join 两个文件"—— GUI 这条路正好把他们逼回去 join。

现有测试没抓住，因为 `test_the_export_is_the_file_a_statistical_test_consumes`（`tests/test_study_view.py:96-112`）只断言了 `lines[0].startswith("driver,phase,laps,best_lap_s")` 和前两个字段，尾部那八个空列不影响断言。

**这条直接影响 4.1 出路 A**：组间比较的可比性论证就靠这几列。修复是一行 + 一条断言（见第 6 节）。

### 4.5 "辅导曝光量"完全没有进数据（**2026-08-27 已修，见 §11**）

审计文件（`<session>/coaching/*.json`，`audit.py:130-148`）记了 provider、model、prompt、原始回答、验证过的报告 —— 这套 provenance 做得很好，但它记的是 **lap 文件名**，不记 participant / phase，也**没有任何"参与者实际看了什么"的记录**：看了几条建议、停留多久、有没有打开 review 窗口逐弯角看、看了哪几个弯。全库搜不到任何曝光/停留时长的字段。

这挡住的是**剂量-反应关系**："看得多的人是不是改善得更多"。在 n 很小的研究里，剂量-反应常常是唯一拿得出手的因果证据 —— 它不受"两组人本来就不一样"的影响，因为比较发生在**接受辅导的人内部**。

### 4.6 样本量与功效没有任何支持

- 每个 phase 3 圈，实际导入通常 3 圈（发车段丢掉，末段靠覆盖率保住）。
- `best_lap_s` 是 3 个数里的最小值 —— **方差大且有偏**（样本越多、最小值越小），两个 phase 圈数不等时尤其危险。**主指标建议用 `mean_lap_s`**，把 `best_lap_s` 作为次要指标报告。
- 没有 power analysis、没有预设的停止规则、没有主指标预注册。这些都不是代码问题，但它们决定了最后那个 p 值有多大意义。

---

## 5. 如果现在就要出结果：一条可行路径

**设计（最小可辩护版本）**

1. 每人：`familiarisation`（不计入）→ `baseline` 3 圈 → 干预 → 第二个 block 3 圈。
2. **随机**把参与者分成两半：一半在第二个 block 前拿 AI 辅导（`--phase coached`），一半拿通用文字或不拿（`--phase control`）。随机化名单在 Apex 之外维护（软件不提供，见 6.6）。
3. 干预前后不要换赛道、车、窗口 —— preset 已经替你保证了。

**采集**

- 辅导组：走 Apex 的 Collect Data 引导页（有防呆），phase 选 `Coached`。
- 对照组：同样走 Collect Data 引导页，phase 选 `Control — second run, own practice only, no AI advice`。（2026-08-25 已实现，见 §8。）

**汇总**

```bash
racecoach collect <participant-archives...> --into pooled/
racecoach study-summary --out summary.csv           # collect 已经把圈入库了
racecoach study-laps    --out laps.csv              # 一行一圈，做学习曲线用
racecoach study-exposure --out exposure.csv         # 一行一次 view，查 dose 是否可信
```

> **2026-08-26 更正**：原来这里写的是 `racecoach study-summary pooled/`，那条命令当时跑不出任何东西 —— pooled 目录里是整场原始导出，不是单圈。现在 `collect` 会把圈注册进 workspace，所以第二条不带参数即可。见 §10.1。

**检验**

| 问题 | 检验 | 备注 |
|---|---|---|
| 辅导组自己前后有没有变化 | Wilcoxon signed-rank（n < 20 或不正态）/ paired t | 主指标 `mean_lap_s`；同时报效应量（Cohen's dz 或 rank-biserial），别只报 p |
| **改善是不是辅导带来的** | 组 × 时间交互：mixed ANOVA，或对 Δ = post − pre 做**两独立样本**检验 | **这一条才是研究问题**，见 4.1 |
| 两组一开始是否可比 | 对四个 `*_rank` 列 + baseline 的 `mean_lap_s` 做组间比较 | 背景列两条路都非空了（§10.1、§10.4） |
| **看得多的人是不是改善得更多** | 在辅导组内部，把 Δ 对 `review_seconds` / `advice_seconds` 做回归（或 Spearman） | 这条比较发生在组内，**不受"两组人本来就不一样"影响**，是 n 小时唯一拿得出手的因果证据。dose 落在 baseline 行（§11.3） |
| 安全性/一致性有没有变 | 同样的检验跑在 `off_track_events`、`damage_events`、`sd_lap_s` 上 | 记得多重比较校正，并预先声明哪个是主指标 |

**空白单元格**必须当成 missing 读进统计软件（`na.strings=""`），不能当 0 —— 这是 `_blank` 那条设计的全部意义。

---

## 6. 建议的改动，按性价比排序

| # | 改动 | 工作量 | 换来什么 |
|---|---|---|---|
| ~~**6.1**~~ **已完成** | `study_view.py` 传上 `backgrounds=`，并把导出测试的断言改成检查整行表头和背景列有值 | — | 修掉 4.4；GUI 导出的文件才真的是"可以直接做检验的文件"。**见 §10.4** |
| ~~**6.2**~~ **已完成** | 采集页下拉框加 control 一项 | — | 对照组能走防呆的引导页，不必让 facilitator 敲命令行。**见 §8** |
| ~~**6.3**~~ **已完成** | `StudyView` 的配对改成"任选两个 phase 比较"，不再硬编码 baseline/coached | — | 修掉 4.2；对照组、多次干预、任何三臂设计都能在界面上看。**见 §9** |
| ~~**6.4**~~ **已完成** | 新增 `racecoach study-laps --out laps.csv`：一行一圈（driver, phase, lap 序号, race_lap, lap_time, off_track, damage + 背景列） | — | 修掉 4.3 —— 学习曲线、混合效应模型、把练习效应剥出来，全靠这个文件。**见 §10.5** |
| ~~**6.5**~~ **已完成** | 审计记录里加 participant / phase 字段；review 窗口记录"打开了哪个弯角、停留多久" | — | 修掉 4.5，拿到剂量-反应这条独立证据链。**见 §11** |
| **6.6** | 随机分配名单：Apex 里生成并锁定 participant → condition 的映射 | 半天 | 现在靠人工名单，容易出错，也没有留痕 |

~~**下一件：6.1**，然后 **6.4**。~~ **两件都在 2026-08-26 做完了（§10）。**对照组采得了（§8）、看得见了（§9）、逐圈那个文件也有了（§10.5）—— 剩下的门槛不在代码里，是人数，以及每段跑几圈（见 §10.10）。

---

## 7. 一句话回答

> 这一节写于 2026-08-25 改动之前。最后半句已经不成立了 —— Study 界面现在自己选比哪两个条件（§9）。其余仍然作数。

**测量、记录、配对、导出 —— 这套软件已经胜任，而且做得比一般的学生项目扎实（条件冻结、身份随文件走、缺失不当零、指标不止圈速）。"是否显著"要你在 R 里按最后一步，这是刻意的设计。真正的短板不在代码里：没有对照组，"变快了"就没法归因于 AI 辅导 —— 而现在的 Study 界面还恰好只认 baseline 和 coached 两个条件，对照组连显示都显示不出来。**


---

## 8. 进展：方案 A 定了，6.2 已经做完（2026-08-25）

**决定：走方案 A —— 加对照组。**先从采集端改起，不让研究负担落到命令行上。

### 8.1 改了什么

`src/apex/capture_pages.py` 的 Study phase 下拉框从三项变成四项。四个标签都重写过，因为原来的 `Baseline — no coaching` 和一个叫 `Control — no advice` 的新选项，读起来是同一句话说两遍：

| 界面上显示 | 写进文件的值 | 是什么 |
|---|---|---|
| Baseline — first run, before any advice | `baseline` | 计入统计的第一段。每个参与者都跑 |
| Coached — second run, after AI advice | `coached` | 计入统计的第二段，看过 Apex 的反馈之后跑 |
| **Control — second run, own practice only, no AI advice** | **`control`** | **计入统计的第二段，但这个参与者被分到对照组：同样的赛道、同样的圈数，两段之间不给任何反馈，全靠自己练** |
| Familiarisation — not measured | `familiarisation` | 熟悉操作，不计入 |

区分这四项的**不是"有没有给建议"，而是"在整个流程的哪一段"** —— baseline 和 control 都没有拿到建议，差别在于一个是第一段、一个是第二段。标签必须把这一点写出来，因为 facilitator 是在参与者已经坐在方向盘前的时候读这个列表的，选错一项就是一整个 session 贴错标签。

默认项没有动，仍然是 `baseline`。

### 8.2 底层不需要改

`phase` 在文件格式里本来就是一个自由 slug（`# phase: <value>`），`racecoach capture-human --phase` 的帮助文字写的就是 "study phase/condition slug"，只做字符合法性校验（`human_capture.py:505-510`），`summarise_all` 按任意字符串分组。所以这次是**只加了一个入口，没有动任何数据通路** —— `control` 的 session 会像其它 session 一样落进 `~/Apex/captures/human/Pxxx-control-<时间戳>/`，被验证、注册、打包、导出。

### 8.3 验证

- 新测试 `test_the_facilitator_can_record_a_control_run_without_the_command_line`（`tests/test_capture_view.py`）断言四个 slug 及其顺序、默认项仍是 baseline、三个标签各自说清了"哪一段 + 给了什么"，并且**选中 Control 之后真正启动采集用的那个 config 里 `phase == "control"`** —— 不是只看下拉框，而是看它流到了哪。
- 这条测试先在改动前的代码上跑过并**确认失败**，再在改动后通过。
- 全套 **637 passed / 17 skipped**（比上一版多的那一条就是它），`ruff check src tests` 干净。
- 参与者安装包 `repos\Apex` 已重新打包换上：把两个 exe 里的 zlib 流充气后搜字符串 —— 新装的 `Apex.exe` 里三个新标签都在、两个旧标签都没了，备份 `Apex.exe.bak-20260825-1135` 正好相反。`racecoach.exe --help` 退出码 0，`Apex.exe` 启动出窗口（标题 'Apex'）并干净退出。
- 换包时你正开着 Apex，所以运行中的 exe 被改名让开（你那个窗口不受影响）。你在 18:19 重开之后确认新进程加载的正是新 `Apex.exe`（哈希与 `dist\Apex\Apex.exe` 完全一致），改名的那份与备份 `bak-20260825-1135` 逐字节相同，已删除。

### 8.4 顺带更新的文档

- `docs/HUMAN_TELEMETRY_CAPTURE.md` 新增 **Study phases** 一节：四项的表，以及为什么对照组是把"辅导"和"练习"分开的唯一办法，还有一句操作纪律 —— **分组要在参与者到场之前定好，phase 不是事后再决定的东西**。
- `CAPABILITY-MAP-synthetic-robot-pilots.md` 的保留 phase 列表加上 `control`（合成机器人的 phase 仍然是 `reference-pilot`，两边不会撞）。

### 8.5 现在卡在哪

**采集这一半通了，展示这一半没有。** `StudyView._paired()` 里 `BASELINE`/`COACHED` 还是硬编码的，所以：

- 对照组参与者会出现在 Study 表格里（每人每 phase 一行，指标齐全）；
- 但他们**永远配不上对**，headline 还会说 "A paired comparison needs the same participant in both 'baseline' and 'coached'"；
- 导出的 CSV **是全的** —— `racecoach study-summary` 按任意 phase 分组，control 的行照样在里面。所以**统计不受影响，受影响的只是界面上看不看得见**。

也就是说：**现在就可以开始收对照组的数据，收到的数据完全可用**；6.3 决定的只是你能不能在 Apex 里直接看到这个对比。下一步建议就做 6.3。


---

## 9. 6.3 也做完了：Study 界面现在自己选比哪两个条件（2026-08-25）

### 9.1 改了什么

`StudyView` 顶上多了一行：**Compare [phase] against [phase]**。两个下拉框列出**这批数据里实际出现过的**条件，按流程顺序排（baseline → coached → control → familiarisation），没见过的 slug 排在后面按字母序 —— 因为 `phase` 在文件格式里本来就是自由字符串，研究有权发明一个这个界面没听说过的条件。

原来 `_paired()` 里那两个硬编码常量没了：现在配对、表头那句话、下面那张圈速图，全都跟着你选的两个条件走（左边蓝、右边绿）。

- **默认**仍然是 `baseline` vs `coached`，所以什么都不动的话界面和以前一模一样。`coached` 不在而 `control` 在的时候，默认第二项就是 `control`。
- **重新 Reload 不会把你正在看的比较挪走** —— 只有当你选的那个条件在新数据里不存在了才会重挑。
- **不允许拿一个条件跟它自己比**：那会让每个人都跟自己配上、差值恒等于 0，是真的但没用。你把左边改成右边那个值时，右边会自动让开，而不是拒绝你刚做出的选择。
- **只有一个条件时**，右边的下拉框是空的、两个都禁用，那句话直接说"这里每一圈都是 `baseline`，配对需要第二个条件"。

### 9.2 为什么这不是"多加一个功能"，而是修一个错

原来那句 `n of N participant(s) have both phases`，在只有一条臂的时候是对的。有了对照组之后它变成误导：对照组的人**没有缺数据**，他们只是被分到了另一条臂上，会在另一种选法下配上对。所以那句话现在把两个条件的名字写出来 —— `2 of 3 participant(s) drove both 'baseline' and 'coached'`，换成 control 就是 `1 of 3 participant(s) drove both 'baseline' and 'control'`。分母是全部参与者，读起来"少了一半"是正常的，因为另一半在另一条臂上。

**这两个数字正是方案 A 的论证要用的**：辅导组自己前后变了多少、对照组自己前后变了多少，比的是这两个数之差。界面把这两个数给你，**但它仍然不做检验** —— 这条立场没动，测试还锁着（界面文字里不许出现 "significant" / "p =" / "p<"）。

### 9.3 验证

- 新增 5 条测试（`tests/test_study_view.py`），核心那条 `test_the_control_arm_is_compared_against_its_own_baseline` 用一个**两条臂、没有人跑满三个 phase** 的工作区：默认选法只配上 A001，切到 `control` 之后只配上 B002，并且读数里出现的是对照组自己那 −0.50 s。其余几条覆盖下拉框的内容与顺序、自己跟自己比时的让位、Reload 不挪动选择、只有一个条件时的禁用状态。
- 旧的那条 `have both phases` 断言按新措辞更新了。
- 这次**没有做"先在旧代码上跑一遍确认失败"**，因为旧代码里 `_selected_phases` 这些根本不存在，失败是 AttributeError，证明不了行为。真正的证据是结构性的：旧 `_paired()` 要求 `BASELINE in phases and COACHED in phases`，而 B002 只有 baseline 和 control —— 它永远进不了那个列表。
- 全套 **642 passed / 17 skipped**，`ruff check src tests` 干净。
- 离屏渲染检查过布局：pickers 那一行在标题行和说明行之间，紧凑靠左，下拉框宽度由最长的那一项自动决定，不会截断。

### 9.4 打包

`repos\Apex` 已重新打包换上并验证：新 exe 里有 `Compare this phase`、`Against this phase`、`_offer_phases`、`One condition against another`，旧的 `have both phases` 和 `Baseline against coached` 都没了，上一步的 Control 选项仍在。

（一个小教训：我一开始还拿新读数里的 `drove both` 当标记，结果它在**旧** exe 里也有 —— 旧 `_paired()` 的 docstring 正好写着 "Participants who drove both phases"，而 **docstring 是会被编译进字节码的，注释不会**。挑标记要挑代码里的名字，别挑给人读的措辞。）`_internal` 逐个哈希比过 —— 只有 `base_library.zip` 因为重打包时间戳不同而不一致，已一并换掉，现在两边完全一致。`racecoach.exe --help` 退出码 0；**带 `APEX_RESEARCH_MODE=1` 启动**（这样 Study Results 才会真的被构造出来）出窗口、干净退出。

### 9.5 你要怎么看到它

Study Results 在**研究模式**下才出现：菜单 **View → Research tools** 勾上。勾上之后顶部导航会多出 Study Results / Robot Pilot / Live Pit Wall 三项 —— 这个开关默认关着是故意的，参与者不该看到这些。顺带把这一项的悬停提示也改了，它原来还写着 "Baseline against coached"。


---

## 10. 真实数据到位之后（2026-08-26）：6.1 和 6.4 做完，另外修了三处只有真实数据才暴露得出来的问题

`win_collect_data/` 里新增了 4 个 handover —— **B0826 的 baseline + coached**（辅导组一个完整被试）和 **C0826 的 baseline + control**（对照组一个完整被试），加上原来的 0823，一共 5 个包、15 圈。数据是在另一台 Windows 机器上用便携包采的（capture manifest 里的 `torcs_binary` 是 `D:\Apex\Apex-Study\torcs-runtime\wtorcs.exe`）。

拿到数据之后第一件事不是写新功能，是**把文档里写的那条路真的走一遍**。走不通。

### 10.1 `collect` → `study-summary` 这条路原本是断的（最要紧的一条）

照 §5 写的做：

```
racecoach collect win_collect_data/*.zip --into pool/
racecoach study-summary pool/ --out summary.csv
```

第一条成功，5 个包逐文件校验通过。第二条：

```
racecoach: No readable laps found to summarise.
```

**而这条命令正是 `collect` 自己在最后一行推荐的下一步。**

原因：参与者交回来的是**采集文件夹**，里面是 TORCS 的整场原始导出（真实数据里是一个 18833 采样、17 MB 的文件），不是切好的单圈。而 study 这一侧每个读取者 —— `study-summary`、Study Results 界面、新的 `study-laps` —— 用的都是 `session_laps()`，它只认规范化的单圈 CSV。切分这一步（`split_torcs_run`）当时**只存在于 GUI 里**：`GarageView.import_handover()`。命令行没有任何入口。

也就是说：**"校验通过"和"能分析"是两个承诺，而软件只兑现了第一个，措辞上却说成了一件事。** 一池完好无损的数据汇总出 0 行，看起来和"什么都没收到"完全一样。

修法是把 GUI 里那段搬到该在的层：`_handover_identity` / `_adopt_background` / 会话命名去重这三段从 `apex/garage_view.py` 移到 `racecoach/telemetry/handover.py`，成为 `handover_identity()` / `adopt_background()` / `session_name()`，再加一个 `register(handover)`。GUI 改成调用同一批函数（它保留自己的 Qt 报错和 fresh 标记），`racecoach collect` 在校验之后调 `register`。这也符合 AGENTS.md 的分层：`src/apex/` 负责渲染，不该独占一整条数据通路的真相。

现在：

```
B0826: 5 files -> ...\handovers\B0826-B0826-baseline-20260826-091209
  3 lap(s) in session B0826-baseline-20260826-091209 (background kept)
  skipped human-1-1787736065-9624-1.csv: ... holds no sample rows.
...
5 handover(s) verified into ...; 15 lap(s) registered in C:\Users\hh25303\Apex\sessions
Next: racecoach study-summary --out summary.csv (or study-laps for one row per lap)
```

**顺带修掉一个隐性的空列问题。** `load_background()` 只从**本机 workspace** 的 `participants/<id>.json` 读。问卷确实随包旅行（`participant.json` 就在 zip 里），但以前只有 GUI 的 import 会把它收编进 workspace。所以研究者在自己机器上跑 CLI 导出时，**背景列同样是空的** —— §5 里那句"走 CLI 导出，背景列才是全的"其实只在"参与者就是在这台机器上采的"时候成立，而那恰恰不是 handover 存在的理由。`register()` 现在收编问卷，两条路都全了。

### 10.2 每一台机器的每一次导出，都混进了一个不存在的参与者

第一次跑通 `study-summary`，第一行是：

```
0822,coached,5,103.56,128.023,13.689,140,268.43,254,,,,,,,,
```

`0822` 不是这个研究招的任何人。它是**软件自带的示例 session**：`ensure_sample_session()` 在每台机器第一次启动时把 `f1coach_core/data/sample_session/` 铺进 workspace，而那五个文件的头部写着 `# driver: 0822 / # phase: coached / # setup: apex-study-v1` —— 它本来就是一份真实的研究采集，`sample.py` 的 docstring 也是这么写的。

后果：**每一台装了 Apex 的机器，导出的 summary 里都会多出一个 coached 组的参与者**，5 圈、mean 128.0 s、sd 13.7、140 次出界、254 次损伤。在目前 n=2 的规模下，这个幽灵占三分之一，而且它的数字比谁都花哨。它还会进 Study Results 的表格，并且把 headline 里 "n of N" 的分母顶大一个。

**不能靠删掉它的身份来解决** —— 那份示例是**故意**带身份的，它同时是"身份跟着文件走"的演示，`test_session.py` 和 `test_garage_view.py` 都锁着这一点。所以修在边界上：新增 `list_study_sessions()`（`workspace.py`），study 这一侧的默认扫描改用它，Garage 仍然用 `list_sessions()`。

> 示例的用途是**演示**；演示不能进证据。把 `sample-session` 显式作为参数指给 `study-summary` 仍然读得到 —— 显式就是显式。

### 10.3 一个空的导出文件会让整批 collect 崩掉

B0826 的 baseline 采集文件夹里有两个 CSV：真正那次跑的 17 MB，和一个 **1584 字节、只有表头、0 行数据**的。时间戳 09:21:05，就在第一次跑结束（09:21:13）前后 —— TORCS 被再开了一次，车还没动就退了。这种文件在采集现场是常态，参与者也没有能力去收拾它。

`split_torcs_run()` 对它做 `np.nanmax(空数组)`，抛出 numpy 的 `ValueError: zero-size array to reduction operation fmax which has no identity`，而这个函数的 docstring 写的是 "Raises TelemetrySchemaError with readable text"。往上 `register` 没有接住，于是**一个人的一个废文件，让另外四个人的数据全都没入库**。

两处修：`split_torcs_run` 对无采样行的导出抛 `TelemetrySchemaError("… holds no sample rows.")`；`register` 跳过读不了的文件并把文件名和原因打出来。后者沿用 `session_laps` 早就写下的原则 —— 一个多出来的坏文件不该让好文件陪葬。

### 10.4 6.1 完成

`study_view.py` 的导出补上 `backgrounds=`，并且把"读问卷"收成视图上的一个 `_backgrounds()`：表格那一列和导出的文件现在用**同一个 mapping**。屏幕上看得见、文件里是空的，正是因为这两处以前各读各的。

导出测试改成断言**整行表头**（`",".join(summary_columns())`）加上背景列有值。**先在改动前的代码上跑过并确认失败**：

```
'A001,baseline,2,19.0,19.5,0.5,5,8.23,,,,,,,,,'.endswith('weekly,3,,,,,25-34,1')  ->  False
```

尾巴上那一串逗号就是 §4.4 描述的八个空列。

### 10.5 6.4 完成：`racecoach study-laps`

一行一圈：

```
driver,phase,lap,race_lap,lap_time_s,off_track_events,off_track_seconds,damage_events,damage_total,source
```

外加八个背景列。两个决定值得写下来：

- **`lap` 是这一圈在这个 phase 里的序号，不是模拟器的圈号。** 模拟器的圈号每次开跑都从 1 重来，而一个 phase 完全可能是分两次录完的（B0826 的 baseline 文件夹里就有两个导出）。排序按文件名走 —— 导出文件名里带这次跑的起始时间戳加补零的圈号，也就是**真正开的顺序**。模拟器那个圈号作为 provenance 单独留一列 `race_lap`。
- **背景列在每一圈上重复。** 刻意的冗余：要把先验经验当协变量放进回归，不该先让分析者去 join 第二个文件。

`damage_total` 也一并给了，phase 级的行没有这一列。

### 10.6 这批真实数据说了什么

先说清楚：**每臂 n=1，什么都证明不了。** 下面只是"管子通了，数字长这样"。

| 参与者 | 臂 | phase | mean_lap_s | best_lap_s | 出界秒数 | 损伤事件 |
|---|---|---|---|---|---|---|
| B0826 | 辅导 | baseline | 124.77 | 119.62 | 120.97 | 90 |
| B0826 | 辅导 | **coached** | **132.16** | 126.12 | 178.82 | 143 |
| C0826 | 对照 | baseline | 116.38 | 105.98 | 123.58 | 168 |
| C0826 | 对照 | **control** | **118.08** | 114.19 | 94.71 | 98 |

两个人的第二段都比第一段**慢**：辅导那位慢 7.4 s，对照那位慢 1.7 s。

但逐圈文件立刻给出一件汇总行看不到的事：

- B0826 的 **coached 三圈是 138.44 → 131.92 → 126.12**，三圈之内快了 12.3 s，是全部 15 圈里最陡的一段下降；
- 而他的 **baseline 三圈是 119.62 → 128.14 → 126.56** —— 第一圈就是他整场最快，之后反而在退。

也就是说"coached 比 baseline 慢"这个汇总数，很可能主要是被 coached 第一圈那个 138.44 拉出来的。这正是 §4.3 讲的那件事：**phase 级的一行没有斜率**，而斜率才是把练习和辅导分开的东西。现在这个文件有了 —— 而它给出的第一条读数，就是汇总行会把人引向反方向。

（另记一笔，方便解释数据：这批 lap 的 `off_track_events` 每圈 19–30 次、`off_track_seconds` 每圈 24–69 s，数字大得可疑，所以回原始通道查过 —— 不是指标算错：一圈里 `|track_pos| > 1` 的采样占 **20.5%**，最远到 ±2.6。Aalborg 窄、参与者是新手，车确实有五分之一的时间在赛道边界外面。这对研究是好消息：安全性指标有足够的区分度，不是一片 0。）

### 10.7 验证

- 全套 **668 passed / 17 skipped**（比上一版多 12 条），`ruff check src tests` 干净。
- 两组新测试做过**改动前的红**：6.1 的导出断言（上面那一行），以及示例 session 的排除（把 `list_study_sessions()` 临时改回 `list_sessions()`，两条都失败）。
- `register` 相关：入库、问卷收编到**另一台机器的 workspace**、重复 collect 不重复计数、没有 manifest 也照样入库、空导出被跳过；以及一条端到端 —— **`collect` 之后 `study-summary` 和 `study-laps` 都返回 0 并写出文件**，锁的正是 10.1 那个断掉的承诺。
- 真实数据上：5 个包 → 15 圈 → 5 行 summary、15 行 laps，背景列非空。
- Study Results 离屏跑过真实 workspace：phase 下拉框给出 `baseline / coached / control`；默认 baseline vs coached 配上 B0826；切到 control 配上 C0826；分母是 3（0823 只有一个 phase），这是 §9.2 说的正常读数。

### 10.8 数据现在在哪

| 东西 | 位置 |
|---|---|
| 原始 handover（校验过的解包） | `C:\Users\hh25303\Apex\captures\handovers\` |
| 可分析的单圈 | `C:\Users\hh25303\Apex\sessions\<参与者>-<phase>-<时间戳>\` |
| 问卷 | `C:\Users\hh25303\Apex\participants\<id>.json` |
| `summary.csv` / `laps.csv` | `D:\apex-analysis\`（D: 是会话主机本地盘，重连不跟人走；要留就自己拷进 profile） |

### 10.9 一件需要你决定的事：参与者数据进了 git

`win_collect_data/0823-baseline-20260823-175257.zip` **已经被提交并推到了 `origin/main`**（commit `8d2f0f3`）。那个 zip 里有一个 61 MB 的 `session.mp4` —— 一位参与者整场的屏幕录像。`.gitignore` 里本来就有 `*.mp4` 和 `/captures/`，意图是清楚的，只是一个 zip 把录像装在里面绕过去了。

我做了的：把 `/win_collect_data/` 加进 `.gitignore`，这样这次新增的 4 个包（含 3 段录像）不会重蹈覆辙。**已经在历史里的那一个我没有动** —— 改写已推送的历史是不可逆而且对外的操作，得你决定。可选的做法，从轻到重：

1. 确认仓库是 private，并接受它留在历史里；
2. `git rm --cached` 那个文件再提交 —— 之后的克隆不再检出它，但**历史里还在**，GitHub 上也还能通过旧 commit 拿到；
3. 用 `git filter-repo` 或 BFG 从历史里彻底抹掉再 force-push，并且请 GitHub 支持清理引用 —— 这是唯一真正删掉的办法，代价是所有克隆都要重来。

如果这份研究有伦理审批，第 3 条大概不是可选项而是必须。

### 10.10 下一步

6.1 和 6.4 做完了，`racecoach study-laps` 已经能吐出学习曲线要的那个文件。剩下的按原顺序：**6.5**（辅导曝光量，拿剂量-反应，**已于 2026-08-27 完成，见 §11**）和 **6.6**（随机分配名单）。

在写代码之前，真实数据先提了一个更便宜的问题：**每个 phase 只有 3 圈，而 B0826 的 coached 三圈还在陡降** —— 也就是说三圈可能根本没跑完他的学习曲线，测到的是"还在适应"而不是"辅导之后的水平"。§4.6 早就说过 `best_lap_s` 在 3 圈上方差大且有偏。要不要把每段加到 5 圈，是比任何一行代码都更能提高功效的改动。

> **2026-08-27 决定（你定的）**：**先不动。**等软件这一轮改完、测试通过之后再调 —— 现在改会让每次软件测试都多跑两圈，采集时间成本落在开发迭代上，不值得。所以 `default_study_preset` 的 `laps=3` 保持原样，**这条留在 6.6 之后再执行**。另外你确认了 §10.6 里那批出界数字是真的：这个模拟器在 Aalborg 上确实容易出界，指标没算错。

---

## 11. 6.5 完成：辅导曝光量现在是被测量的（2026-08-27）

§4.5 那条堵住的不是一个功能，是**这个规模的研究唯一拿得出手的因果证据**。两臂各 1 个人，组间比较在设计上就没有功效；而"看得多的人是不是改善得更多"这个比较发生在**接受辅导的人内部**，不受"两组人本来就不一样"的影响。它的前提只有一个：知道每个人到底看了多少。以前一个字都没记。

### 11.1 审计记录现在说得清是谁

`write_coaching_audit`（`src/f1coach_core/audit.py:116-160`）多了三个字段：`driver` / `phase` / `setup`，从 `lap.identity` 来。

以前它只记 lap **文件名**。文件名不是参与者：导入时会改名、会跟别人的池子混在一起、几个月后读它的人当时不在场。而这套审计文件同时也是**"这个参与者到底被告知了什么"的唯一记录** —— 要把"辅导组被告知了什么"和"他们后来怎么开"对起来，起点必须是这一趟是谁的。

### 11.2 新增 `src/f1coach_core/exposure.py`：曝光量这件事本身

一个 Qt-free 的模块，369 行。核心是三件事：

**一、`ReviewView`** —— 一次"某个东西在某人面前待了多久"：`driver / phase / kind / corner / lap / seconds / advice / findings / at / id`。`kind` 只有两种：

| kind | 是什么 | 为什么分开 |
|---|---|---|
| `corner` | review 窗口开在某一个弯角上 | |
| `report` | 分析页右侧 AI Race Engineer 面板上有一份 findings | **有人把面板读完了但一个弯角都没点开，这是真实存在的一类人。**合并成一个数就把这类人藏了 |

**二、`ExposureLog`** —— 开始 / 暂停 / 继续 / 结束的一台钟。时钟是注入的（`clock=time.monotonic`），所以秒数可以被断言而不用等；用 monotonic 而不是墙上时钟，笔记本中途跳表也算不出负数。

**三、聚合与列**：`phase_exposure(views, phase)` → 五列

```
review_seconds        弯角 review 的总秒数
review_corners        看了几次弯角（次数）
review_corners_seen   看了几个不同的弯角（广度，不是深度）
advice_seconds        其中"屏幕上确实有一条 AI 指令"的秒数
report_seconds        findings 面板本身在面前的秒数
```

### 11.3 三条被刻意划出来的线

**一、`phase` 是"他在看哪一段的圈"，不是"他接下来跑哪一段"。**
参与者看的是自己 baseline 的圈，然后去跑第二段。所以 dose 落在 **baseline 那一行**，response 是 coached 那一行的成绩。如果他跑完第二段之后又回头看了第二段的圈，那份曝光**来得太晚，不可能是任何东西的原因** —— 它落在 coached 行上，跟前一份分得干干净净，而不是加在一起。

**二、空白 ≠ 0，而且这一次分界线在"有没有日志"上。**
- **没有日志文件** → 五列全空。含义是"没人记录过他看了什么"：旧版本打的包、这个功能之前采的数据、或者对照组参与者两次跑之间根本没打开过 app。统计软件按 missing 读，是对的。
- **有日志但里面没有 view** → 五列全 0。含义是"记录了，他什么都没看"。**这是一个测量结果。**

把前者读成后者，等于把"最不投入"的读数发给恰好没人记录的那批人 —— 这跟 `_blank` 那条规则是同一条规则。为此 `package()` 现在**总是**塞一个 `exposure.jsonl` 进包里，哪怕是空的：这个版本会记录，所以它有资格说"这个人什么都没看"。

**三、两台钟不重叠，所以可以相加。**
review 窗口弹出来的时候，findings 面板其实还在它后面。但**没有人隔着一个窗口读下面的面板**，而两个会重叠的量，分析的人一定会去加。所以 `AnalysisView._open_review` 里显式把 report 那台钟**暂停**，review 窗口一关（`reviewClosed`）再**继续**。真实数据上跑出来：report 2.45 s + 弯角 2.6 s ≈ 脚本里总共等的 5.0 s。

### 11.4 记什么、不记什么

- **不记的**：低于 `MIN_VIEW_SECONDS = 1.0` 的 view 一律不写。用方向键划过弯角列表是按键，不是注意力，把按键算成注意力会**恰好虚高剂量-反应所依赖的那个数**。理由和 `MIN_EXCURSION_SAMPLES = 5` 是同一条。
- **不记的**：软件自带的 sample session。它是 5 圈**真实**的圈，头部带着**真实**参与者 `0822` 的身份 —— 也就是 §10.2 那个坑。研究者点开 demo 看两眼，就会被记成"0822 在读他自己的辅导"。`is_recordable()` 按 session 文件夹名把它挡掉（`sample-session` 和包内的 `sample_session` 两个名字都挡）。
- **不记的**：没有身份的圈。dose 要么属于某个参与者，要么不属于任何人。
- **诚实的边界**（写在模块 docstring 里）：这测的是**屏幕上有什么**，不是**读进去了什么**。后者没人测得了，把前者叫做"注意力"，和把缺失通道当 0 是同一种越界。

### 11.5 它得跟着优盘走，不然不算研究数据

日志在参与者的 workspace 里（`~/Apex/exposure/<id>.jsonl`），不在采集文件夹里 —— 跟问卷一模一样的处境，所以走的是跟问卷一模一样的路：

1. `package()` 把它作为 `exposure.jsonl` 塞进包（问卷是 `participant.json`）；
2. `adopt_exposure()` 在研究者这边把它收编进 workspace，`register()`（CLI）和 `GarageView.import_handover()`（GUI）**两条路都调**；
3. **按 view 的 `id` 取并集**去重。这一条是必须的：每个包带的是**到打包那一刻为止的整份日志**，不是"这一段的份额"，所以同一个人的第二个包会把第一个包里的每一条都再带一遍。直接 append 就会**恰好给参与阶段最多的那些人重复计数**。

> **顺带修了一个既有缺陷**：`package()` 原来用 `rglob("*")` 收所有文件，再单独追加 workspace 里的 `participant.json`。如果被打包的文件夹里**已经**有一个 `participant.json`（一个被解包后又重新打包的 handover 就是这样），归档里会出现两个同名成员；而 `unpack` 是**按名字**建 digest 表的，两个里只有一个静悄悄地生效 —— digest 本来要抓的"文件损坏"正好被这个盖住。现在 `RESERVED_NAMES` 把 `handover.json` / `participant.json` / `exposure.jsonl` 三个名字从 rglob 里排除掉。这个缺陷是**跑真实数据时才冒出来的**（`UserWarning: Duplicate name`），不是想出来的。

### 11.6 分析者手上多了什么

```bash
racecoach study-summary  --out summary.csv    # 多了 5 个 dose 列
racecoach study-laps     --out laps.csv       # 同样 5 列，随参与者重复
racecoach study-exposure --out exposure.csv   # 新命令：一行一次 view
```

GUI 的 `Export CSV…` 也一并带上了（`study_view.py`）—— 这正是 §4.4 那个 bug 的形状，不能让它以新的形式重演一遍，所以测试里加了断言。

**为什么还要 `study-exposure` 这个原始文件**：跟 6.4 要 `study-laps` 是同一个理由。聚合出来的 dose 只有在"窗口当时确实在被读"的前提下才是 dose，而聚合本身看不见**一次开着过了一顿午饭的 review**、**一分钟内被点开四十次的同一个弯**、或者**每个弯都看了就是漏了一个**。这些是剂量-反应结论能不能站住之前必须做的检查，而只有一行一次 view 的文件做得了。

### 11.7 验证

- **`698 → 700 passed, 17 skipped`**，ruff 干净。新增 12 条 exposure 测试，加上 replay window 4 条、analysis view 3 条、handover 5 条、garage 1 条、study 3 条、audit 2 条。
- **红过再绿**（不是声称，是真跑了）：拆掉 `_open_review` 里那次 `paused()` → 报告那条从 15.0 s 变成 315.0 s；拆掉 `adopt_exposure` → GUI 导入那条 `KeyError: 'P007'`；拆掉 `study_view` 的 `exposure=` → 导出那条 `'' != '52.0'`；拆掉 sample 守卫 → 两条 demo 测试都真的记下了 `driver='0822'`。
  - 中途发现**我自己写的两条 sample 守卫测试原本是假绿的** —— 时钟一秒没走，view 被 1.0 s 的下限丢掉了，跟守卫在不在无关。补上 `clock.tick(120.0)` 之后才真的在测那个守卫。
- **真实数据上跑通了整条链**（`B0826-baseline`，真telemetry）：分析页出 findings → 点开 T1 → 读到 T2 → 关掉 review → 关掉 app，得到

```
driver,phase,kind,corner,lap,seconds,advice,findings,at
B0826,baseline,corner,T1,human-1-...-lap01.csv,1.4,1,,2026-08-27T08:46:26+00:00
B0826,baseline,corner,T2,human-1-...-lap01.csv,1.2,1,,2026-08-27T08:46:27+00:00
B0826,baseline,report,,human-1-...-lap01.csv,2.45,1,3,2026-08-27T08:46:28+00:00
```

  再打包 → 换一个从没见过这个人的 workspace → `collect` + `register`：`3 lap(s), background=True, 3 view(s)`，dose 正确落在 baseline 行；**第二次 collect 同一个包，仍然是 3 view(s)**。
- **对现有五个参与者的行**：五列全部**空白**。正确 —— 那五个包是这个功能之前采的，没人记录过他们看了什么。`racecoach study-exposure` 直接说出来："Nothing was recorded, which is not the same as nobody having looked."

### 11.8 跑打好的 exe 时又抓到一个（2026-08-27 下午）

把**冻结的 `racecoach.exe`** 拉过一遍完整链路（打包 → collect → register → 导出），
结果对不上：写进日志的是 **3 条 view，收进去只有 2 条**，而且丢的恰好是第一条。

原因是我自己的测试脚本用 PowerShell 5.1 的 `Set-Content -Encoding utf8` 写的日志 ——
它会写 BOM（`EF BB BF`）。`json.loads` 吃不下带 BOM 的那一行，`read_views` 就把它当
成一条坏行跳过了。跳过坏行是**故意的**；**因为一个编码标记而跳过一条完好的记录不是**，
而且它没有任何东西可以指出来。

产品侧确实有问题，而且比曝光量这一处严重得多：**所有从别人机器上过来的文件**都是按
plain `utf-8` 读的 —— `manifest.json`、`participant.json`、`exposure.jsonl`。Windows 上
任何重写过这些文件的东西（记事本另存、同步客户端、某些杀软）都可能留下 BOM。对
`manifest.json` 来说，后果是 `handover_identity()` 返回 `(None, None)` ——
**整个 handover 变成一堆没有身份的圈，任何比较都用不了它**，而且没有一句报错。

改成 `utf-8-sig`（就是 utf-8 加上"开头有 BOM 就丢掉"），四处读取点全改。两条新测试
都**红过再绿**：不改的话，exposure 那条丢第一条 view，handover 那条直接
`assert (None, None) == ('P007', 'baseline')`。

这条是**跑冻结的 exe 跑出来的，不是读代码读出来的** —— 和 §10.1 是同一个教训。

### 11.9 这不改变的事

n 还是每臂 1 个人。剂量-反应需要的是**辅导组内部有足够的曝光量差异**，一个人给不出任何差异。这一节做的是**从下一个参与者开始，这条证据链有数据可用**；已经采的那五个包补不回来。
