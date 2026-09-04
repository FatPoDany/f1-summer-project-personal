# Review 界面的双圈回放：按位置对齐，还是按时间对齐？

> **English summary — *Two laps side by side: align them by track position, or by
> elapsed time?*** A working note in the language it was thought in, started from
> the question asked on 2026-08-24 about the review window, where two clips play
> with a track map between them.
>
> The answer is **by distance along the track, not by time since the start.**
> Aligning by time puts the two cars at different places on the circuit within a
> few seconds, which is exactly the comparison a driver cannot learn from;
> aligning by position keeps both cars at the same corner and shows the time
> difference as a number instead. Sections 2 and 3 take the three layers apart —
> the track map, the two videos, and why the points are frame-accurate while the
> video is corrected — and set out what position alignment costs.
>
> Section 4 records the four changes that came out of answering it: a cumulative
> delta-t readout, low-frequency re-anchoring during playback, clamping that no
> longer fails silently, and the regression tests added. Section 5 indexes the
> code, section 6 the risks that remain, section 7 how the changes reached the
> participant build, and section 8 a loop-playback bug that one of the fixes
> introduced, with its repair.

> 起点是 2026-08-24 的问题：review 窗口里两个视频左侧的轨迹图，是按**相同赛道位置**播放两圈的车，还是按**发车后相同时间**播放两车？以及哪一种更有效。
>
> 第 1–3 节回答这个问题。第 4 节记录据此做出的四处改动（4.5 是你报告的抖动的根因），第 5 节是代码索引，第 6 节是我对这次工作的反馈和仍然存在的风险，第 7 节记录这些改动怎么进的参与者安装包 `C:\Users\hh25303\repos\Apex`，第 8 节是 4.5 自己带进来的循环播放 bug 及其修复。
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
# track_replay.py, TrackReplay._advance / _step
def _advance(self) -> None:
    elapsed = self._clock.nsecsElapsed() / 1e9      # QElapsedTimer，从锚点起算
    self._step(elapsed - self._last_frame_s)
    self._last_frame_s = elapsed

def _step(self, seconds: float) -> None:
    t = self._lap.df["t"]
    self._playhead_s += seconds                     # 播放头以圈自己的秒为准
    index = int(t.searchsorted(self._playhead_s, side="left"))
    self._slider.setValue(min(index, self._last))
```

定时器只决定**多久重画一次**（约 30 fps），走多远由**真实时钟**决定。这段代码原来不是这样写的，而且是错的 —— 它按帧数记时，实测只有真实速度的 0.842×，那正是你看到的抖动的根因，见 **4.5**。改过之后绿点是**你当时开车的真实速度**在重放 —— 你在这个弯里磨蹭了多久，屏幕上就磨蹭多久。

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

- `TrackReplay._step()` 每累计 `RESYNC_S = 0.5` **秒圈时间**发一次 `driftCheckDue` 信号（原来是数帧 —— 帧不是时间，见 4.5）。标记本身看不见画面，所以它只负责“该看一眼了”，不断言任何事。
- `ReplayWindow._resync_footage()` 收到后，问每个画面 `FootagePane.drift_ms(moment)` —— 播放器当前位置与"标记所在时刻应该对应的 clip 位置"之差，两边都是 clip 自己的媒体时间，所以变速播放的那一路也是按它自己的速率该走到的地方来衡量的。
- 只有偏差超过 `RESYNC_TOLERANCE_MS = 250` 的那一路才会被 seek 回去。已经在位的画面不动 —— 每帧 seek 正是共享 transport 一开始要避免的卡顿。
- **一次纠正没生效，就不再纠正这个画面**（限本弯角）。seek 本身要花一次可见的卡顿，只有当它买来“此后是对齐的”才划算。下一次检查发现同一个画面还是偏的，说明这次纠正什么也没买到，继续每半秒抖一下只是白抖。

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

## 4. 据此做出的改动

三条建议都已实现（4.1–4.3）。**4.5 是第四处：你报告的抖动的根因与修复。**全套 **634 passed / 17 skipped**，`ruff check .` 干净。

### 4.1 读数里加一条累计 delta-t

- `TrackReplay._prepare_delta()` 在每次 `set_stretch` 时调一次 `f1coach_core.time_delta`，存下 `(grid, delta)`；`ValueError` 时置空。
- `TrackReplay._show_delta_readout()` 每帧用 `np.interp` 查一个点，写进新的 `_delta_readout` 标签。
- 遵守 `AGENTS.md` 的分工：`src/apex` 渲染 core 的结果，不自己算遥测真值。

**运行时验证**（offscreen，`shot.py`）：同一对合成圈，标记从弯角起点走到终点，读数依次是 `0.67 → 0.89 → 1.11 s behind lap02 by this point`，与 `2 × d / 900` 的解析解一致 —— 数字确实跟着标记走，不是一个静态值。

### 4.2 播放中低频重锚

- `TrackReplay` 新增信号 `driftCheckDue`，每累计 `RESYNC_S = 0.5` 秒圈时间发一次；`_anchor_clock`（新建 stretch、按下 Play、任何用手挪动标记的地方）会重置计数，因为那些地方本身就是一次锚定。
- `FootagePane.drift_ms(wall_clock)` 是新的测量口：播放器当前位置 − 该时刻应在的位置，单位 ms，正数表示画面跑到标记前面去了。没加载、放不下位置时返回 `None` 而不是 0 —— "不知道自己在哪"不能报告成"我在位"。
- `ReplayWindow._resync_footage()` 是策略口：只在播放中执行，跳过还在切片的画面，只 seek 偏差超过 `RESYNC_TOLERANCE_MS = 250` 的那一路，并且**同一个画面纠正一次不见效就停手**（见 4.5）。

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
| `test_playback_asks_for_a_drift_check_a_few_times_a_second` | 每 `RESYNC_S` 秒一次，不是每帧 |
| `test_playback_advances_by_the_time_the_driver_actually_took` | 播放头是圈时间，不是采样计数（棘轮） |
| `test_the_marker_runs_at_real_time_rather_than_at_a_count_of_frames` | 对着真实时钟量：标记必须跟真实时间一致 |
| `test_a_picture_that_will_not_stay_put_is_left_alone_after_one_try` | 纠正无效就停手，换个弯角重新判断 |
| `test_a_picture_that_settles_is_corrected_again_when_it_wanders_again` | 停手只针对失败的纠正 |
| `test_the_marker_carries_on_from_where_a_key_put_it_mid_playback` | 键盘/点滑轨挪动标记后，播放从那里继续，不被拽回 |
| `test_a_picture_that_has_drifted_is_pulled_back_onto_the_marker` | 只 seek 漂了的那一路 |
| `test_nothing_is_reseated_while_the_marker_is_paused` | 暂停时不动画面 |
| `test_drift_is_measured_against_where_the_marker_says_the_picture_should_be` | `drift_ms` 的正负号与量纲 |
| `test_a_pane_with_nothing_loaded_reports_no_drift` | 返回 `None` 而不是 0 |
| `test_a_rate_too_far_apart_to_hold_says_the_picture_cannot_keep_step` | clamp 时标题多一行，且不覆盖圈名 |

---

### 4.5 第四处改动：标记改用真实时钟 —— 你报告的抖动的根因

4.2 上线后你的反馈是：**画面每半秒左右抖一下，改动之前是流畅的。**这是真的，起因也确实是 4.2 —— 但不是 4.2 本身写错了，而是它把一个**一直存在、此前没人看得见**的 bug 变成了看得见的卡顿。

**根因：标记的时钟从来就不是真实时间。**原来的 `_advance` 有两个误差叠在一起：

| 误差 | 机制 | 实测 |
|---|---|---|
| 棘轮 | `searchsorted(t + 33 ms, side="left")` 落在**大于等于**目标的第一个采样上，下一帧又从那个采样重新起算。真实遥测是 50 Hz（20 ms 一个采样），于是每个 33 ms 都被向上取整成 40 ms | **+21%** |
| 定时器粒度 | Windows 上 33 ms 的 `QTimer` 实际每 **47 ms** 才触发（系统时钟 15.6 ms 的整数倍）| **−29%** |

净效果实测 **0.842×**：标记比它声称在重放的那次驾驶**慢 16%**，而旁边的视频走媒体时钟，是准的。两者以每秒约 160 ms 的速度分开 —— 每次检查（500 ms）就攒下约 110 ms，超过当时 150 ms 的容差。**于是每一次检查都判定“漂了”，每一次都 seek，每一次 seek 都是一次可见的卡顿。**你看到的半秒一抖，就是检查的节拍本身。

顺带说明：在 4.2 之前，这个 bug 也一直在 —— 只是没有任何东西去比对，所以它表现为“画面和标记越走越不同步”，而不是“抖”。**它牺牲的是同步，4.2 把它换成了抖动。**两者都不对。

**修法：标记按时钟走，不按帧数走。**

- 播放头 `_playhead_s` 以圈自己的秒为单位独立保存，不再从“落在哪个采样上”反读 —— 每帧向上取整一次是**棘轮**，不是舍入。
- 每帧推进多少，由 `QElapsedTimer` 从锚点起算的**真实流逝时间**决定，而不是名义上的 33 ms。
- 用 `nsecsElapsed()` 而不是 `restart()`：后者只返回整毫秒，每帧丢掉的余数本身就是一个慢 1% 的时钟。
- 锚点在**任何非播放本身造成的移动**处重置：新建 stretch、按下 Play（暂停的时间不是开车的时间）、拖滑块、方向键、点滑轨、循环回头。这一条是做完之后运行时验证时补上的 —— 播放头独立保存意味着它**必须**被告知标记被挪到了别处，否则下一帧就会把标记拽回去。只认拖拽（`sliderMoved`）会漏掉键盘和点滑轨。

**实测（offscreen，8 秒，真实 sample session 的圈）：**

| | 标记速率 | 每次检查攒下的漂移 |
|---|---|---|
| 修复前 | 0.842× | −111 ms |
| 只改棘轮 + 毫秒时钟 | 0.985× | −8 ms |
| 现在（纳秒、从锚点起算） | **0.998×** | **−1 ms** |

**端到端验证**：把一个“完全听话、按真实时间播放、诚实报告自己位置”的模拟播放器接到真实的 `ReplayWindow` 上跑 8 秒。它收到的每一次 seek 都是标记造成的漂移，也都是一次可见的卡顿：

| | 检查次数 | 最大漂移 | 判定“漂了” | 实际 seek（= 可见卡顿） |
|---|---|---|---|---|
| 你装的那一版（数帧，容差 150 ms） | 10 | +251 ms | 5 | **5** |
| 现在 | 15 | +68 ms | 0 | **0** |

**transport 的运行时验证**（offscreen，真实 sample session 的圈）：

| 操作 | 结果 |
|---|---|
| 播放 1.0 s | 标记走了 **+0.98 s** 圈时间 |
| 暂停 1.5 s | 标记走了 **+0.00 s**（暂停不是开车） |
| 继续播放 1.0 s | 标记走了 **+1.00 s**（不是 +2.5 s —— 没有把暂停的时间补上） |
| 拖滑块后播放 1.0 s | 从**放下的位置**起走了 +1.00 s |
| 播到 stretch 末尾 | 回到开头，继续循环 |
| 播放中用键盘挪动标记 | 从**挪到的位置**继续，没有被拽回 |

上面那张端到端的表是 offscreen、不绘图的**下限**。真实窗口每帧还要重绘轨迹图，定时器只会更晚，标记只会更慢，漂移只会更大 —— 这就是为什么你实际看到的频率比这张表还高。

**另外两道保险**（因为这台机器上仍然没有真实视频可测）：

- `RESYNC_TOLERANCE_MS` 从 150 提到 **250**。播放器报告自己的位置有它自己的粒度，容差低于那个粒度，等于把舍入误差当成漂移去纠正。
- **同一个画面纠正一次不见效，本弯角内就不再纠正它。**seek 要花一次可见的卡顿，只有当它买来“此后是对齐的”才划算；如果下一次检查发现同一个画面还是偏的，那要么这个后端报不准自己的位置，要么这两圈在这个弯里根本没法用一个速率跟住 —— 两种情况下继续每半秒 seek 一次，都只是白抖。换一个弯角重新判断，不会一路写死。

**第二道保险的意义**：即使我对根因的判断仍然不完整，或者真实后端还有别的问题，最坏情况也**只抖一次**，不会再退化成节拍器。

---

## 5. 代码索引

| 关注点 | 位置 |
|---|---|
| 绿点按真实时间推进 | `track_replay.py` → `TrackReplay._advance` / `_step` |
| 标记的时钟与锚点 | `track_replay.py` → `_anchor_clock`、`_playhead_s`、`QElapsedTimer` |
| 蓝点按里程定位 | `track_replay.py` → `TrackReplay._reference_index` |
| 累计时间差：预计算 / 每帧读取 | `track_replay.py` → `_prepare_delta` / `_show_delta_readout` |
| 漂移检查的节拍 | `track_replay.py` → `RESYNC_S`、`driftCheckDue` |
| 漂移的测量 | `footage_pane.py` → `FootagePane.drift_ms` |
| 漂移的纠正策略 | `replay_window.py` → `ReplayWindow._resync_footage`、`RESYNC_TOLERANCE_MS` |
| 纠正失败后停手 | `replay_window.py` → `_corrected` / `_left_alone` |
| 走完一圈重新开始 | `track_replay.py` → `TrackReplay._restart`（见 8.1） |
| 重锚，以及它清掉的判决 | `replay_window.py` → `ReplayWindow._anchor_footage`（见 8.2） |
| 参考视频变速与 clamp | `replay_window.py` → `MatchedRate`、`_matched_rate`、`RATE_FLOOR/CEILING` |
| clamp 的可见提示 | `replay_window.py` → `ReplayWindow._caption_the_reference` |
| 两段 clip 各按各自时钟裁 | `replay_window.py` → `ReplayWindow.show_stretch` |
| 弯角 → 墙钟窗口 | `f1coach_core/footage.py` → `window_for` |
| `dist` 的来源（赛道坐标 vs 积分） | `f1coach_core/torcs.py`；`f1coach_core/loader.py` |
| 累计 delta 的唯一真值 | `f1coach_core/features.py` → `time_delta` |
| 测试 | `tests/test_track_replay.py`、`tests/test_replay_window.py`、`tests/test_footage.py` |

---

## 6. 我的反馈：仍然存在的风险和我没做的事

三条建议都实现了，另外修掉了你报告的抖动（4.5）。以下是仍然值得盯的事，以及我做错的一处判断 —— 6.1 是对我自己上一版反馈的更正。

### 6.1 我预测对了症状，归因错了 —— 这一条必须更正

原文写的是：如果后端报告的位置系统性滞后超过容差，就会每 500 ms 抖一次；并建议把 `RESYNC_TOLERANCE_MS` 调到 250–400。

**症状确实发生了，但原因不是我写的那个。**真正的原因在标记自己的时钟：它以 0.842× 的速度跑，视频是准的，所以那些“漂移”里绝大部分是**标记造成的**，而不是画面漂了。按原文的建议调大容差**不会修好它** —— 漂移是持续累积的，任何固定容差都会在几秒内被突破，只是把抖动的间隔拉长。

**我该记住的**：这件事在这台机器上**可以直接测出来**（十几行脚本，不需要任何视频、任何 Qt 之外的东西），而我当时把它写成了一条“只能在真实视频上验证”的猜测，还顺手给了一个治标的处方。**能测的先测**，不要把可测的东西归到不可测的那一侧。

**仍然没变的部分**：这台机器上依然没有真实录制文件，`drift_ms` 对真实 `QMediaPlayer` 的行为依然没有被验证过。4.5 的端到端验证用的是模拟播放器，它按定义是诚实的；真实后端未必。

**残留风险**：如果 WMF 报告位置的粒度大于 250 ms，第一次检查仍可能误判并 seek 一次。之后“纠正一次不见效就停手”会接管，所以最坏情况是**一次**卡顿，不是一串。

**怎么判断**：打开一个对比 review，按 Play 看满一个弯角。

- 全程流畅 → 好了。
- 开头抖一次、之后正常 → 就是上面这个残留风险，把 `RESYNC_TOLERANCE_MS` 提到 400。
- 又变成节拍器 → 说明还有我没找到的第三个原因。请告诉我，不要自己调容差 —— 那是在盖住症状，这次已经证明了这样会盖住什么。

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

严格说，低频重锚是**纠正**而不是**预防**。真正的预防是把一段 clip 按里程切成若干子区间、每段用自己的速率播，那样中段就不会偏。我没有这么做，因为：复杂度明显更高（要管理多段速率切换、切换点的抖动），而收益上限只是把偏差从实测的几十毫秒再压小。**如果将来发现 150 ms 在实际教学中确实会误导人，那才是做分段变速的时候** —— 到那时 `drift_ms` 已经能给出实测数据来支持这个决定，现在还没有。

---

## 7. 这些改动进到参与者拿到的那个包里了吗？（2026-08-25）

> 你的问题：`C:\Users\hh25303\repos\Apex` 里体现了我的改动吗？

**你问的那一刻：没有。现在：进去了，而且逐项验证过。**

### 7.1 你问的时候，包里装的是哪一版

`C:\Users\hh25303\repos\Apex` 不是源码，是参与者实际运行的成品目录（`Apex.exe`、`racecoach.exe`、`_internal\`，加上不参与重建的 `torcs-runtime\`、`ffmpeg\`、`granite-runtime\`）。第 4 节的改动全部写在 `C:\Users\hh25303\repos\f1-summer-project-personal\src\` 下，**当时没有重新打包**，所以包里跑的仍是旧代码。

时间线本身就说明了问题：

| 时刻（2026-08-24） | 发生了什么 |
|---|---|
| 22:21 | 打包 —— 含 4.1–4.3，不含 4.5 |
| 22:22 | 拷进 `repos\Apex` |
| （之后） | **你运行它，报告“每半秒抖一下”** |
| 22:48 / 23:01 | 我改 `replay_window.py` / `track_replay.py` —— 即 4.5 的修复 |

修复比包里那个 exe 晚了半小时。但文件日期不算证据（两份构建从外面看一模一样），所以用的是硬办法：把 exe 里所有 zlib 流解压出来，直接搜索只属于某一版的标识符。三份构建的结果并排放在一起：

| 标识符 | `Apex.exe.bak-20260823` | 你测试的那份 | 现在包里的 |
|---|---|---|---|
| `driftCheckDue`（4.2 的低频重锚） | 无 | **有** | **有** |
| `RESYNC_FRAMES`、`_frames_since_resync`（按帧计数的旧时钟） | 无 | **有** | 无 |
| `RESYNC_S`、`_anchor_clock`、`_playhead_s`、`nsecsElapsed`、`_left_alone`（4.5 的修复） | 无 | 无 | **有** |

这张表把三份构建和你的三次体验对上了：

- `bak-20260823`：没有重锚，也就没有 seek —— **你记忆里那份“流畅的”**；代价是标记和画面越走越不同步，只是没有东西去比对，看不出来。
- 你测试的那份：有重锚，时钟仍按帧计数（实测 0.842×）—— **每次检查都判定漂了，每次都 seek，每次 seek 一抖**。你报告的现象由此完全解释，也再次印证 4.5 的归因。
- 现在包里的：有重锚，时钟改成真实时间（实测 0.998×）—— 检查照常，但没有东西可纠正。

### 7.2 我做了什么

按 memory 里记的那套流程走的，没有跳步：

1. `pytest` —— **634 通过、17 跳过**；`ruff check src tests` —— 全部通过。
2. `.venv\Scripts\pyinstaller apex.spec --noconfirm`。
3. 备份被替换的那一版：`Apex.exe.bak-20260824`、`racecoach.exe.bak-20260824`（`bak-20260823` 那一份原样保留）。
4. 拷 `Apex.exe`、`racecoach.exe`，并镜像 `_internal\`。`torcs-runtime\`、`ffmpeg\`、`granite-runtime\`、`uninstall.exe`、`apex-study-build.txt` **一个都没动**。

镜像之前先做了空跑：两棵 `_internal` 各 1074 个文件，文件集合完全一致，**没有任何文件会被删掉**。逐文件哈希对比后只有 `base_library.zip` 不同，而那是标准库压缩包重打时间戳的结果 —— 也就是说，这次真正变化的只有两个 exe 里的那段代码，195 MB 的依赖一个字节都没变。

### 7.3 换完之后验证了什么

| 检查 | 结果 |
|---|---|
| 包里 `Apex.exe` 的标识符 | 旧的两个已消失，4.5 的五个全部出现（上表最后一列） |
| `racecoach.exe --help` | 正常输出、退出码 0 —— 冻结的运行时和 `_internal` 是好的 |
| 双击 `Apex.exe` | 10 秒内出窗口（标题 `Apex`），请求关闭后**干净退出，退出码 0** |

### 7.4 还需要你做的，以及怎么退回去

**需要你确认的**：抖动的修复在这台机器上是靠测量和离屏端到端验证的，**没有人在真实录像上看过它**（这台机器上没有录制文件）。所以判断标准还是 6.1 结尾那三条 —— 现在请在**换过之后的包**上重跑一次：打开一个对比 review，按 Play 看满一个弯角。全程流畅就是好了；只在开头抖一次是 6.1 说的残留风险；又变成节拍器请告诉我，别自己调容差。

**退回去**：把 `Apex.exe.bak-20260824` 和 `racecoach.exe.bak-20260824` 改回原名即可 —— `_internal` 不用动，因为它没变。再往前一版是 `bak-20260823`（重锚之前的那份）。

**后续**：你在换过之后的这个包上又发现了循环播放的问题 —— 那是 4.5 自己带进来的，见第 8 节；包已经再换过一次。

**一件要知道的事**：这个包是用**尚未提交**的工作区改动打出来的（`AGENTS.md` 规定不经你要求不提交）。也就是说现在源码树一旦被回退，包和源码就对不上了。要我提交的话说一声。

---

## 8. 循环播放坏了：进度条走完之后（2026-08-25）

> 你的报告：进度条走完之前一切正常；走完之后视频继续播，但左边轨迹图和下面的参数全部静止，按钮显示 Pause，进度条停在最左边不动；再过一阵两个视频都黑屏；然后左边视频重新开始播、进度条和数据也开始动，但右边视频还是黑的；等左边播完，两个视频又一起开始播，进度条和数据却又不动了 —— 如此反复。

**这是我的 bug，而且是 4.5 那次修复自己带进来的。**两个原因叠在一起，正好长成你描述的那个交替。已修复、已加回归测试、已重新打包。

### 8.1 第一个原因：走完一圈之后，标记倒着走了一整段

4.5 把标记改成读真实时钟之后，`_advance` 是这样的：

```python
elapsed = self._clock.nsecsElapsed() / 1e9
self._step(elapsed - self._last_frame_s)
self._last_frame_s = elapsed          # <- 记在 _step 之后
```

平时没问题。但走到头时，`_step` 会调用 `_restart()` 回到起点，而 `_restart` 必须**把时钟重新起算**（因为圈时间从这一段的末尾跳回了开头）—— `_anchor_clock` 于是执行了 `_clock.restart()` 并把 `_last_frame_s` 归零。

**然后这一帧回到 `_advance`，把它自己那个读数写了回去** —— 而那个读数来自刚刚被换掉的那只时钟。于是下一帧算出来的是：

```
新时钟的 0.047 秒  −  旧时钟的 1.66 秒  =  −1.61 秒
```

播放头一口气退回到这一段开始之前一整段的位置。此后每帧只推进 0.047 秒，所以它要**花掉与这一段等长的时间**才能爬回起点。这段时间里 `searchsorted` 一直落在起点之前，滑块被量程钳在最左端，`valueChanged` 不再触发，读数也就不再刷新 —— **进度条停在最左边、数据静止、按钮却仍然是 Pause，因为播放确实还在跑**。

离屏实测（一段 1.66 秒的弯角，连播 5 秒，每 40 ms 采一次滑块位置）：

| | 修复前 | 修复后 |
|---|---|---|
| 第一遍 | 0.00 → 1.67 s 正常 | 同样正常 |
| 回到起点之后 | **卡在第一个采样上 1.69 秒** | 无停顿 |
| 5 秒内跑完几遍 | 1 遍 + 一次长时间静止 | **3 遍，连续** |
| 最长静止 | 1.69 s | 无超过 0.3 s 的静止 |

1.69 秒 ≈ 这一段的长度 1.66 秒 —— 这个数本身就是证据。

**修法**是把记账挪到步进之前，让"重新起算"永远是最后说话的那个：

```python
elapsed = self._clock.nsecsElapsed() / 1e9
seconds = elapsed - self._last_frame_s
self._last_frame_s = elapsed          # 先记，再走
self._step(seconds)
```

### 8.2 第二个原因：右边那个画面被永久判了死刑

4.5 还加了一条"纠正一次不见效就不再纠正这个画面"（见 4.5 与 6.1）。它防的是"每半秒抖一下"的节拍器，本身是对的 —— 但那个判决**一直留到这个弯角结束**，只有换弯角才清除。

于是：标记静止的那一整段时间里，两个画面在自顾自地播，漂移瞬间就超过容差；第一次检查把它们各拉回一次，第二次检查发现还是偏的（当然还是偏的，标记根本没动），**两个画面就都被写进了"不再管"名单**。等标记终于恢复走动，没有任何机制会再去管它们 —— 谁的片子刚好还在播就能看见，播完的那个就一直黑着。这就是"右边视频黑屏、而且不会自己回来"。

**修法**：判决的有效期是**这一趟**，不是这个弯角。`_anchor_footage()` 是"把每个画面都放到标记所在的时刻"这件事唯一发生的地方 —— 按 Play、拖进度条、走完一圈重新开始，都会经过它。既然刚刚重新对过一次，之前关于"这个画面稳不住"的结论就已经过期了，所以在那里清空。节拍器的防护没有削弱：一趟当中没有任何东西会调用 `_anchor_footage`，所以一趟里最多仍然只纠正一次。

### 8.3 两个原因合起来，正好是你看到的那个交替

| 阶段 | 标记 | 两个画面 |
|---|---|---|
| 第一遍 | 正常走完 | 正常，跟着走 |
| 走完之后（一段的长度） | 停在最左边，数据静止 | 被重锚到弯角开头后自由播放，很快被写进"不再管" |
| 再往后 | 仍然静止 | 各自播到片尾，播放器停住 → **两个都黑** |
| 标记爬回起点 | 恢复走动，数据也动 | 没人再管它们；只有还没播完的那个有画面 |
| 走完，再来一遍 | 又停住 | 重锚使它们又开始播 |

—— 与你写的顺序逐条对上。

### 8.4 为什么原来的测试没抓住

已有的那条 `test_playback_goes_round_again_rather_than_stopping_at_the_end` 是直接调 `_step()` 的，而 bug **恰恰在 `_advance` 和 `_step` 之间的那笔账上**。测试绕开了出问题的那条缝。

新增两条，都先在打回原状的代码上跑过、确认会失败：

| 新测试 | 它守住什么 | 在旧代码上 |
|---|---|---|
| `test_the_marker_carries_straight_on_after_it_has_gone_round` | 用**真实定时器**跑过一次循环，等它回到起点后再等 400 ms，标记必须已经离开起点 | 失败：`assert 250 > 250` |
| `test_a_picture_written_off_is_given_another_chance_next_time_round` | 被判"不再管"的画面，在下一趟重锚之后必须重新获得一次机会 | 失败 |

**教训**：写测试要驱动生产代码真正走的那条路。`_step()` 好测，但没人在真的用它 —— 定时器用的是 `_advance`。

### 8.5 验证与打包

- `pytest`：**636 通过、17 跳过**（比昨天多的 2 条就是上面两条）；`ruff check src tests` 全过。
- 重新打包并换进 `C:\Users\hh25303\repos\Apex`，已验证：

| 检查 | 结果 |
|---|---|
| 从 exe 里把 `_advance` 反汇编出来 | 换之前：先 `CALL _step` 再 `STORE_ATTR _last_frame_s`；**换之后：先 `STORE_ATTR` 再 `CALL`** |
| 8.2 那处修复的标记 | 在包里的 exe 中找到 |
| `racecoach.exe --help` | 退出码 0 |
| 启动 `Apex.exe` | 出窗口，请求关闭后干净退出 |

这次只搜标识符是不够的：8.1 的修复**只调换了两行的顺序**，没有新增任何名字，注释又根本不进编译结果。所以这次是把 exe 里那个模块反 marshal 出来、直接反汇编 `_advance` —— 字节码本身就是最终的证据。

**换包时你正开着 Apex**，Windows 不允许覆盖正在运行的 exe。`racecoach.exe` 和 `_internal\` 已经换好；`Apex.exe` 用改名的办法让开（运行中的进程不受影响），新的拷进原位；你关掉程序之后我确认那个改名的副本与备份逐字节相同，已删除。现在包里有三层备份：`bak-20260823`（重锚之前）、`bak-20260824`（抖动那版）、`bak-20260825-0917`（循环坏掉那版）。

### 8.6 现在请你再试一次

打开一个对比 review，**按一次 Play，让它自己转三圈以上**，重点看：

1. 每一圈之间是否无缝 —— 进度条回到最左边之后应当立刻继续走，不该有停顿。
2. 两个视频是否每一圈都跟着回到弯角开头，右边那个不该黑掉不回来。
3. 顺带确认 6.1 那条：整程是否流畅、开头有没有抖一次。

如果第 2 点仍然出问题（某个画面黑了不回来），那说明 `QMediaPlayer` 播到片尾停住之后 `seek_to` 里的那次 `play()` 没有生效 —— 那是这台机器上测不到的一段，请告诉我具体是哪一边、第几圈开始的。

### 8.7 复测结果（2026-08-25，你亲自跑的）

8.6 三条**全部通过**：圈与圈之间无缝、两个视频每圈都跟着回到弯角开头、右边不再黑掉不回来、整程流畅且开头没有再抖。

也就是说 §4 那三条改动到这里才算真正落地：一次重锚（4.1）、按需查漂移（4.2）、真时钟（4.5），加上本节修掉的两处回环缺陷。目前 `repos\Apex` 里的那一版就是这个状态，可以拿去跑参与者。

一句留档：这两个缺陷都只在**走完一整段之后**才现形，而当时的测试要么直接调 `_step()`（绕开了出问题的那笔账），要么根本没跑到第二圈。以后再动播放时钟或者画面纠正那套逻辑，测试请务必**连着转两圈以上**，别只测第一圈。
