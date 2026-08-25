# 我们的软件能否证明"接受了 AI 赛后辅导的人开得更好了"？

> 起点是 2026-08-25 的问题：**目前这套软件能不能拿来对比"接受了 AI 赛后反馈辅导的玩家"和"他最开始跑的那几圈"，判断是否有显著提升？**
>
> 下面每一条结论都指向具体代码位置，不凭印象。第 1 节是结论，第 2 节列已经具备的能力，第 3 节讲软件刻意不做的那一件事，第 4 节是真正挡在"显著"前面的六个问题（其中 4.4 是一个实打实的 bug），第 5 节给一条现在就能走的路径，第 6 节是按性价比排序的改动建议。

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

结论（已更新）：**采集这一半已经通了** —— 对照组现在走同一个防呆的引导页，不必敲命令行。**剩下的是展示这一半**：control 的数据会完整地落盘、也会被 `racecoach study-summary` 导出，但 Apex 的 Study 界面仍然只会把 `baseline` 和 `coached` 配成对，对照组的人会出现在表里却永远配不上。这就是 6.3，现在它是下一个卡点。

### 4.3 导出粒度只到 phase 级

一人一 phase 一行，能做配对 t / Wilcoxon，**做不了**：

- **学习曲线**（第几圈 × 是否辅导）—— 而这正是把练习效应从辅导效应里剥出来的手段；
- **混合效应模型**（lap 嵌套在 participant 里）—— 在 3 圈 × n 人这种小样本下，这是最能榨出统计功效的做法，因为它用的是所有圈而不是每人两个汇总数；
- **把先验经验当协变量的回归** —— 背景列有，但只挂在 phase 级的行上。

原始 lap CSV 全都在盘上（`~/Apex/sessions/<name>/*.csv`，每个文件自带 driver/phase），所以**这些分析都做得了，只是软件现在不给那个文件**，要自己写一段脚本遍历 session 目录。

### 4.4 GUI 导出把背景列丢了 —— 这是一个 bug

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

### 4.5 "辅导曝光量"完全没有进数据

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
racecoach study-summary pooled/ --out summary.csv   # 走 CLI，背景列才是全的
```

**检验**

| 问题 | 检验 | 备注 |
|---|---|---|
| 辅导组自己前后有没有变化 | Wilcoxon signed-rank（n < 20 或不正态）/ paired t | 主指标 `mean_lap_s`；同时报效应量（Cohen's dz 或 rank-biserial），别只报 p |
| **改善是不是辅导带来的** | 组 × 时间交互：mixed ANOVA，或对 Δ = post − pre 做**两独立样本**检验 | **这一条才是研究问题**，见 4.1 |
| 两组一开始是否可比 | 对四个 `*_rank` 列 + baseline 的 `mean_lap_s` 做组间比较 | 需要背景列非空 → 走 CLI 导出，或先修 4.4 |
| 安全性/一致性有没有变 | 同样的检验跑在 `off_track_events`、`damage_events`、`sd_lap_s` 上 | 记得多重比较校正，并预先声明哪个是主指标 |

**空白单元格**必须当成 missing 读进统计软件（`na.strings=""`），不能当 0 —— 这是 `_blank` 那条设计的全部意义。

---

## 6. 建议的改动，按性价比排序

| # | 改动 | 工作量 | 换来什么 |
|---|---|---|---|
| **6.1** | `study_view.py:239` 传上 `backgrounds=`，并把导出测试的断言改成检查整行表头和背景列有值 | **一行 + 一条断言** | 修掉 4.4；GUI 导出的文件才真的是"可以直接做检验的文件" |
| ~~**6.2**~~ **已完成** | 采集页下拉框加 control 一项 | — | 对照组能走防呆的引导页，不必让 facilitator 敲命令行。**见 §8** |
| **6.3** | `StudyView` 的配对改成"任选两个 phase 比较"（下拉选 A/B），不再硬编码 baseline/coached | 半天 | 修掉 4.2；对照组、多次干预、任何三臂设计都能在界面上看 |
| **6.4** | 新增 `racecoach study-laps --out laps.csv`：一行一圈（driver, phase, lap 序号, lap_time, off_track, damage + 背景列） | 半天 | 修掉 4.3 —— 学习曲线、混合效应模型、把练习效应剥出来，全靠这个文件 |
| **6.5** | 审计记录里加 participant / phase 字段；review 窗口记录"打开了哪个弯角、停留多久" | 1–2 天 | 修掉 4.5，拿到剂量-反应这条独立证据链 |
| **6.6** | 随机分配名单：Apex 里生成并锁定 participant → condition 的映射 | 半天 | 现在靠人工名单，容易出错，也没有留痕 |

**下一件：6.3**（对照组已经能采了，却在 Study 界面上看不到），然后 6.1（一行，且现在导出的文件是有缺陷的）。**要拿到能发表的因果结论，6.4 + 一个对照组是最低门槛。**

---

## 7. 一句话回答

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
