# Review 界面的双圈回放：按位置对齐，还是按时间对齐？

> 起点是 2026-08-24 的问题：review 窗口里两个视频左侧的轨迹图，是按**相同赛道位置**播放两圈的车，还是按**发车后相同时间**播放两车？以及哪一种更有效。
>
> 第 1–3 节回答这个问题。第 4 节记录据此做出的三处改动，第 5 节是代码索引，第 6 节是我对这次工作的反馈和仍然存在的风险。
>
> 相关代码：`src/apex/widgets/track_replay.py`、`src/apex/widgets/replay_window.py`、`src/apex/widgets/footage_pane.py`、`src/f1coach_core/footage.py`

---

## 1. 结论

**按相同的赛道位置（distance along track）对齐，不是按发车后的相同时间。**

更准确的说法是一个**主从结构**：

- **主时钟**是"你这一圈"自己的真实时间 —— 绿点以 1× 真实速度重放你当时的驾驶；
- **参考圈是被投影上去的** —— 蓝点不用自己的时间，它每一帧都去参考圈里查"当绿点跑到里程 *d* 时，那一圈在里程 *d* 处是哪个采样点"，然后画在那里。

所以屏幕上任意一瞬间，**两个点永远在赛道的同一个位置上**。它们之间在屏幕上的间距不是"谁领先多少"，而是**两条走线的横向差异** —— 你跑宽了、他切得更内。

还有一点值得先说清楚：这个 review 窗口**根本不是整圈回放**，它是**逐弯角**的。窗口顶部的列表是 "Corners, in the order they are driven"，每次只播 `point.span_m[0] → point.span_m[1]` 这一段。读数里的 `+x.x s` 是从**这个弯角的起点**算起，不是从起跑线算起。所以"发车后相同时间"这个模式在这个界面里连语义都不成立。

---

## 2. 三层机制拆解

### 2.1 左侧轨迹图（`TrackMap` / `TrackReplay`）

| 元素 | 颜色 | 怎么动的 |
|---|---|---|
| 灰线 | `TEXT_DIM` | 你这一圈的完整轨迹 |
| 紫线 | `PURPLE` | 当前讨论的那一段弯角（加粗） |
| 蓝虚线 | `BLUE` | 参考圈的完整轨迹 |
| **绿点** | `GREEN` | **按真实时间推进** |
| **蓝点** | `BLUE` | **按里程查表定位** |

地图下面是四行读数：图例（两圈各自的名字）、你自己的仪表行（`+x.x s · km/h · throttle · brake · gear`，dim）、参考圈在同一位置的速度（blue）、以及累计时间差（dim，见 2.4）。

**绿点（this lap）—— 真实时间：**

```python
# track_replay.py, TrackReplay._advance
def _advance(self) -> None:
    """Step by the real time the driver took, so playback matches the drive."""
    t = self._lap.df["t"]
    target = float(t.iloc[self._slider.value()]) + FRAME_MS / 1000.0   # FRAME_MS = 33
    index = int(t.searchsorted(target, side="left"))
    self._slider.setValue(min(index, self._last))
```

30 fps 的定时器，每帧把目标时间往前推 33 ms，再在 `t` 列上二分查找。也就是说绿点是**你当时开车的真实速度**在重放 —— 你在这个弯里磨蹭了多久，屏幕上就磨蹭多久。

**蓝点（comparing with）—— 纯里程，完全不看时间：**

```python
# track_replay.py, TrackReplay._reference_index
def _reference_index(self, distance: float) -> int:
    """The reference lap's sample at the same point on track."""
    ref_dist = self._reference.df["dist"]
    return int(min(ref_dist.searchsorted(distance, side="left"), len(ref_dist) - 1))
```

绿点每移动一帧，`_update_readout()` 就拿绿点当前的 `dist` 调一次这个函数，把蓝点放到参考圈的同一里程处。**每帧都查，所以蓝点的位置对齐是逐帧精确的，永远不会漂。**

`dist` 的来源决定了"同一位置"有多严格：TORCS 导出的圈直接用仿真器的 `dist_from_start_m`（`torcs.py`），这是**沿赛道中心线的起跑线距离**，是真正的赛道坐标；只有当数据源没有这一列时，loader 才退而用速度积分补出来（`loader.py`，此时 `Lap.dist_derived = True`），那种情况下 `dist` 变成"各自跑过的路程"，走外线的车会累积得更快，位置对齐随之退化。研究用的 TORCS 数据走的是前一条路。

另外查表用的是 `searchsorted(..., side="left")`，**取最近采样点，不做插值**，所以对齐精度上限是参考圈的一个采样间隔（60 Hz、150 km/h 下约 0.7 m）。对这个用途完全够。

### 2.2 两个视频

两段 clip 是**各自从各自司机的墙钟里裁出来的**：

```python
# replay_window.py, ReplayWindow.show_stretch
self._windows = windows_for(lap, self._points)
# The same corners located in the compared lap's own recorded time: it
# drove them at a different moment, and for the two clips to be of the
# same piece of track each has to be cut from its own driver's clock.
self._ref_windows = windows_for(reference, self._points) if comparing else {}
```

`windows_for()` 的做法是：先在 `dist` 上找到弯角的首尾采样，再读那两个采样的 `wall_clock_s`，得到一段墙钟区间。**入口是里程，出口才是时间** —— 又一次的位置对齐。

因为两圈开同一个弯用的秒数不同，参考圈的视频还要被**变速**（`_matched_rate`，见 2.3）。baseline 数据里的 Turn 7：一圈 6.6 s，另一圈 14.7 s，比率 **2.227×**。这个数字本身就是"为什么不能按时间播"的最好证据 —— 如果两个视频都 1× 实时并排放，第一个画面出弯的时候，第二个还没跑完这个弯的一半。

左边你自己的视频永远是 1×（`set_rate` 只对 `_ref_footage` 调用），因为主时钟就是它。

Play / 拖动滑块时两个播放器同时重新锚定：

```python
# replay_window.py, ReplayWindow._anchor_footage
moment = self._replay.current_wall_clock                    # 你的墙钟
self._footage.seek_to(moment)
reference = self._replay.current_reference_wall_clock       # 参考圈在同一里程处的墙钟
self._ref_footage.seek_to(reference)
```

而 `current_reference_wall_clock` 内部又是走 `_reference_index()` —— 还是里程。

### 2.3 点是逐帧精确的，视频是被纠正的

蓝**点**每帧查表，位置对齐精确、不漂。参考**视频**做不到这一点：它只在按 Play、拖滑块、循环回头时被锚定，中间以恒定速率播放，而恒定速率等于把"两圈的时间-里程关系"当成**线性**。实际上它不是 —— 一个入弯早刹、出弯追回的司机，在这段 clip 的首尾都是对齐的，中段却可以偏出几十到几百毫秒。

现在这件事是**被测量并纠正的**，而不是被无视的：

- `TrackReplay._advance()` 每 `RESYNC_FRAMES = 15` 帧（约 500 ms）发一次 `driftCheckDue` 信号。标记本身看不见画面，所以它只负责"该看一眼了"，不断言任何事。
- `ReplayWindow._resync_footage()` 收到后，问每个画面 `FootagePane.drift_ms(moment)` —— 播放器当前位置与"标记所在时刻应该对应的 clip 位置"之差，两边都是 clip 自己的媒体时间，所以变速播放的那一路也是按它自己的速率该走到的地方来衡量的。
- 只有偏差超过 `RESYNC_TOLERANCE_MS = 150`（30 fps 下约 4.5 帧）的那一路才会被 seek 回去。已经在位的画面不动 —— 每帧 seek 正是共享 transport 一开始要避免的卡顿。

速率的 clamp 也不再是静默的。`_matched_rate` 返回一个 `MatchedRate(rate, clamped)`，一旦真实比率落在 `[0.25, 4.0]` 之外，右侧画面的标题会**多出一行黄色的说明**，逐弯角判定：

> ● comparing with — lap02 · 1.000 s
> these two laps took too differently long through this corner for the picture to keep step with the marker

没有这行字的时候，画面就是跟得上标记的。

### 2.4 累计时间差

位置对齐主动丢掉的那一维现在被写回读数里了。地图下方第四行（dim，因为这个数字不属于任何一圈）：

```
0.67 s behind lap02 by this point
1.11 s ahead of lap07 by this point
level with lap02 by this point
```

数字来自 `f1coach_core.time_delta` —— **和 Compare 界面画的是同一条曲线**，所以两个界面不会对同一米给出不同的差值。每个 stretch 预计算一次，标记每帧用 `np.interp` 查一个点。

三个刻意的选择：

- **"level" 而不是 "0.00 s behind"**：`behind` 是一个断言，四舍五入到 0.00 的测量支持不了它。
- **两圈距离太短、无法放在同一个网格上时什么都不显示**。`time_delta` 会抛 `ValueError`，这里接住并保持沉默 —— 替代方案是一个看起来像测量、实际是外推的数字。
- **dim 而不是绿/红**。在这个窗口里 GREEN = 这一圈、BLUE = 参考圈，是身份颜色；用绿色表示"你领先"会和"这是你的数"撞车。措辞已经把方向讲清楚了，不需要颜色再讲一遍。

---

## 3. 哪一种更有效？

**结论：对于教学复盘，按位置对齐是对的，这个项目选对了。** 但两种模式回答的其实是两个不同的问题，值得摊开讲。

### 3.1 两种模式各自回答什么问题

| | 按位置对齐 | 按时间对齐 |
|---|---|---|
| 回答的问题 | **同一个弯，两台车做了什么不一样的事？** | **到现在为止，差距是多少？** |
| 两个画面里的内容 | 同一段路缘石、同一个刹车牌、同一个 apex | 赛道上的两个不同地方 |
| 差异表现为 | 走线差、速度差、刹车点差、油门接入时机差 | 屏幕上的间距（= 时间差） |
| 典型用途 | 数据分析、教练复盘、找可执行的改进点 | 电视转播、ghost car、"他在追上来吗" |
| 可执行性 | 高 —— 每个差异都对应一个具体动作 | 低 —— 只告诉你输了，不告诉你哪输的 |

### 3.2 为什么教学场景必须用位置对齐

1. **只有同一段路才可比。** 教学的整个前提是"你在这里做了 A，他在这里做了 B"。如果两个画面不在同一个地方，就没有"这里"，也就没有比较可言。上面 Turn 7 那个 6.6 s vs 14.7 s 的例子已经说明，实时并排在这个数据集上是直接崩掉的，不是"稍微不同步"。

2. **时间差被翻译成了可执行的量。** 位置对齐之后，"慢了 0.4 s"这个不可执行的事实，会自动显现为"你的刹车点早了 15 m""你在 apex 的速度低了 12 km/h"这些可执行的事实。

3. **行业惯例是一致的。** MoTeC i2、AiM Race Studio、Cosworth Pi Toolbox —— 正经的赛车数据工具默认 x 轴一律是 **distance**，不是 time。时间只在算 delta-t 曲线时出现。原因就是这一条：时间轴上两圈的同一点不可比，距离轴上才可比。这不是审美，是这个领域几十年的共识。

4. **参与者是驾驶员，不是观众。** 这个 review 窗口的 docstring 自己讲得很清楚：*"A participant does not need to be told how their lap went — they drove it — they need to know that at Turn 3 they braked too early and what to do about it."* 目标是行为改变，不是结果告知。

### 3.3 位置对齐的代价

- **慢的那台车的画面被加速播放，动作会看起来比真实更急。** 2.2× 速率下，人对"他的方向盘动得多快""他刹车多果断"的直觉判断是失真的。这是位置对齐固有的代价，没法消除；能做的只是在补偿失效时说出来（2.3 的黄色提示）。
- **中段漂移只能纠正，不能消除。** 现在的做法是每 500 ms 检查、超过 150 ms 才拉回，所以设计上就容忍 150 ms 以内的偏差。要根除得把一段 clip 切成多个子区间各自定速，复杂度不成比例。
- **屏幕上原本看不到"你落后了多少秒"。** 这一条已经补上了（2.4）。

### 3.4 什么时候时间对齐才是对的

只有一种情况：观众要看的**就是差距本身随时间的演化**。转播里的 ghost car、"他还有 3 圈能不能追上"这类叙事。参与者复盘不属于这一类。

### 3.5 现在的形态：位置对齐做主，时间差单独显示成一个数

这是两种模式的正解，也是这个界面现在的形态：画面和点按位置锁死，同时把"到这里为止累计落后 0.43 s"作为一行字显示出来。两个问题都回答了，而且不牺牲任何可比性 —— 因为时间差是被**写出来的**，不是被**播出来的**。

---

## 4. 据此做出的三处改动

三条都已实现、有回归测试，全套 630 passed / 17 skipped，`ruff check .` 干净。

### 4.1 读数里加一条累计 delta-t

- `TrackReplay._prepare_delta()` 在每次 `set_stretch` 时调一次 `f1coach_core.time_delta`，存下 `(grid, delta)`；`ValueError` 时置空。
- `TrackReplay._show_delta_readout()` 每帧用 `np.interp` 查一个点，写进新的 `_delta_readout` 标签。
- 遵守 `AGENTS.md` 的分工：`src/apex` 渲染 core 的结果，不自己算遥测真值。

**运行时验证**（offscreen，`shot.py`）：同一对合成圈，标记从弯角起点走到终点，读数依次是 `0.67 → 0.89 → 1.11 s behind lap02 by this point`，与 `2 × d / 900` 的解析解一致 —— 数字确实跟着标记走，不是一个静态值。

### 4.2 播放中低频重锚

- `TrackReplay` 新增信号 `driftCheckDue`，`_advance()` 每 `RESYNC_FRAMES = 15` 帧发一次；`_toggle`（开始播放）和 `_restart`（循环回头）会重置计数，因为那两处本身就是一次锚定。
- `FootagePane.drift_ms(wall_clock)` 是新的测量口：播放器当前位置 − 该时刻应在的位置，单位 ms，正数表示画面跑到标记前面去了。没加载、放不下位置时返回 `None` 而不是 0 —— "不知道自己在哪"不能报告成"我在位"。
- `ReplayWindow._resync_footage()` 是策略口：只在播放中执行，跳过还在切片的画面，只 seek 偏差超过 `RESYNC_TOLERANCE_MS = 150` 的那一路。

测量在 `FootagePane`（它拥有播放器），策略在 `ReplayWindow`（它拥有这对画面的配对关系），标记只负责报时 —— 它看不见画面，所以它不该断言画面在哪。

### 4.3 clamp 不再静默

- `_matched_rate` 现在返回冻结 dataclass `MatchedRate(rate: float, clamped: bool)`，`clamped` 为真当且仅当真实比率被 `[RATE_FLOOR, RATE_CEILING] = [0.25, 4.0]` 截断。
- `ReplayWindow._caption_the_reference(clamped=...)` 逐弯角重写右侧画面的标题：基础文本（圈的名字）不变，clamp 时追加一行黄色说明。同一对圈可能在这个弯跟得上、在下一个弯跟不上，所以这是 per-corner 而不是 per-review 的判断。

**运行时验证**：20 s vs 1 s 的一对圈（比率 0.05，远超 clamp），标题渲染为名字 + 黄色警告；0.5 的正常比率下没有这行字。

### 4.4 新增的回归测试

| 测试 | 锁住的行为 |
|---|---|
| `test_the_readout_says_how_much_time_has_gone_by_this_point` | 数字与 `time_delta` 一致，方向正确，反过来读是 "ahead of" |
| `test_two_laps_that_took_the_same_time_are_called_level_not_zero` | 不用 "0.00 s behind" 断言一个方向 |
| `test_a_lap_with_no_reference_quotes_no_gap` | 没有第二圈就没有差值 |
| `test_a_reference_too_short_to_share_a_grid_quotes_no_gap` | `time_delta` 拒绝时保持沉默，但蓝线和速度读数照常 |
| `test_playback_asks_for_a_drift_check_a_few_times_a_second` | 每 `RESYNC_FRAMES` 一次，不是每帧 |
| `test_a_picture_that_has_drifted_is_pulled_back_onto_the_marker` | 只 seek 漂了的那一路 |
| `test_nothing_is_reseated_while_the_marker_is_paused` | 暂停时不动画面 |
| `test_drift_is_measured_against_where_the_marker_says_the_picture_should_be` | `drift_ms` 的正负号与量纲 |
| `test_a_pane_with_nothing_loaded_reports_no_drift` | 返回 `None` 而不是 0 |
| `test_a_rate_too_far_apart_to_hold_says_the_picture_cannot_keep_step` | clamp 时标题多一行，且不覆盖圈名 |

---

## 5. 代码索引

| 关注点 | 位置 |
|---|---|
| 绿点按真实时间推进 | `track_replay.py` → `TrackReplay._advance` |
| 蓝点按里程定位 | `track_replay.py` → `TrackReplay._reference_index` |
| 累计时间差：预计算 / 每帧读取 | `track_replay.py` → `_prepare_delta` / `_show_delta_readout` |
| 漂移检查的节拍 | `track_replay.py` → `RESYNC_FRAMES`、`driftCheckDue` |
| 漂移的测量 | `footage_pane.py` → `FootagePane.drift_ms` |
| 漂移的纠正策略 | `replay_window.py` → `ReplayWindow._resync_footage`、`RESYNC_TOLERANCE_MS` |
| 参考视频变速与 clamp | `replay_window.py` → `MatchedRate`、`_matched_rate`、`RATE_FLOOR/CEILING` |
| clamp 的可见提示 | `replay_window.py` → `ReplayWindow._caption_the_reference` |
| 两段 clip 各按各自时钟裁 | `replay_window.py` → `ReplayWindow.show_stretch` |
| 弯角 → 墙钟窗口 | `f1coach_core/footage.py` → `window_for` |
| `dist` 的来源（赛道坐标 vs 积分） | `f1coach_core/torcs.py`；`f1coach_core/loader.py` |
| 累计 delta 的唯一真值 | `f1coach_core/features.py` → `time_delta` |
| 测试 | `tests/test_track_replay.py`、`tests/test_replay_window.py`、`tests/test_footage.py` |

---

## 6. 我的反馈：仍然存在的风险和我没做的事

三条都实现了，但有几件事你应该知道，其中两条是我**没能验证**的。

### 6.1 重锚从未在真实视频上跑过 —— 这是最需要盯的一条

测试里 `drift_ms` 是被 stub 掉的，运行时验证也只跑到"没有 clip 时返回 `None`"。这个环境里没有真实的录制文件，我也不打算为了跑通它去生成一段假视频 —— 那证明不了真实后端的行为。

**具体的失效模式**：`QMediaPlayer.position()` 在不同后端上的更新粒度不同。如果 Windows 的 WMF 后端报告的位置**系统性地滞后超过 150 ms**，那么每次检查都会判定"漂了"并 seek，结果就是每 500 ms 卡一下 —— 恰好是这条改动本来要避免的东西。

**怎么判断**：拿一段真实 session 打开一个对比 review，按 Play 看满一个弯角。如果画面每半秒抖一下，就是这个问题。
**怎么修**：把 `RESYNC_TOLERANCE_MS` 调大（250–400）。它是具名常量就是为了这个。真要根除则需要在 seek 前后读一次位置来估计后端的报告延迟，但在看到症状之前不值得写。

### 6.2 "by this point" 和弯角列表里的 "0.44 s lost" 是两个数，可能被混读

屏幕上现在同时有：

- 弯角列表：`3. T3 · braking — 0.44 s lost`（**这个弯**丢掉的时间）
- 读数行：`0.67 s behind lap02 by this point`（**从起跑线到这里**累计的时间）

两个都对，含义不同，全靠 "by this point" 这几个字做区分。我认为措辞够了，但这是**需要在参与者身上验证的措辞判断，不是可以靠代码保证的事**。如果试用时有人把两个数当成一回事，最省事的改法是把读数行改成 `0.67 s behind lap02 since the start line`，把参照点写死。

### 6.3 累计曲线的起点继承自 `time_delta`，我没有动它

`time_delta` 的网格永远从 0 m 开始（`np.arange(0.0, end, 5.0)`），对于 `dist` 不从 0 附近开始的圈，最初几米是 `np.interp` 的边界钳制值。这是既有行为，Compare 界面一直在用；我刻意没有改，因为改了就会让两个界面对同一米给出不同的数 —— 而"两处一致"正是这次改动的目的之一。如果哪天要修，应该在 `f1coach_core` 里修一次，两个界面同时受益。

### 6.4 我没有碰蓝点的逻辑

它本来就是对的 —— 逐帧按里程查表，没有可改进的地方。这次所有改动都在"视频"和"读数"两侧。

### 6.5 关于第 2 条改动本身的一点保留

严格说，低频重锚是**纠正**而不是**预防**。真正的预防是把一段 clip 按里程切成若干子区间、每段用自己的速率播，那样中段就不会偏。我没有这么做，因为：复杂度明显更高（要管理多段速率切换、切换点的抖动），而收益上限只是把已经压到 150 ms 以内的偏差再压小。**如果将来发现 150 ms 在实际教学中确实会误导人，那才是做分段变速的时候** —— 到那时 `drift_ms` 已经能给出实测数据来支持这个决定，现在还没有。
