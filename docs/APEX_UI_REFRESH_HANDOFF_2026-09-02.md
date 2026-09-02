# Apex UI 全面改进交接记录（2026-09-02）

## 本次目标与结果

本次工作只调整 Apex 桌面端的视觉层、页面布局和产品范围，没有更改遥测数据契约、确定性分析、Granite 证据校验、采集协议或报告计算逻辑。

完成后的产品主线是：

```text
Collect Data → Garage → Session Debrief / Lap Analysis → Compare
                                      └→ evidence-grounded AI Coach
Research mode → Study Results
```

桌面端已移除不属于当前赛后复盘主线的 **Live Pit Wall** 和 **Robot Pilot**。底层 live/synthetic CLI 与 `racecoach` 实验代码仍保留，可用于复现实验；没有在论文阶段做高风险的技术性大拆。

## 与队友 IBMF1 的对齐基准

开始工作前执行了 `git fetch --prune origin`，读取的是队友仓库 `IBMF1/origin/main` 的最新状态：

- commit：`bda5455`
- 时间：2026-09-01 22:38:22 +0100
- 当前团队方向：`Player Kit → Coach API → deterministic review pipeline → Review Web`
- 视觉基准：`apps/review-web/src/theme.css` 与 `app.css`

Apex 是 PySide6 桌面应用，IBMF1 Review Web 是 React 应用，因此没有强行合并两套技术栈，而是对齐了产品方向和设计语言：暖灰石墨表面、低饱和边框、instrument amber 强调色、克制的圆角和层级、无霓虹和发光。

## 保留的 Apex 优点

- 暗色遥测界面和高信息密度。
- 紫色表示 session best / reference。
- 绿色表示 gain，红色表示 loss / risk。
- 当前圈与参考圈叠加、距离对齐、共享光标和证据区间高亮。
- Corner Breakdown 的确定性技术提示、双击回放和可追溯证据。
- Granite 只解释冻结证据包，模型不控制车辆。
- Audit、Session Debrief、Compare 和 Study Results 均保留。

## 视觉改进内容

### 1. 全局设计系统

`src/apex/theme.py` 现在集中管理：

- warm-graphite 的 4 层背景和两级边框；
- 主文本、次文本、弱文本；
- amber 主强调色与 Apex 原有的紫/绿/红遥测语义；
- Menu、Toolbar、StatusBar、Button、Input、ComboBox、List、Table、GroupBox、ProgressBar、Scrollbar、Tooltip 和 Splitter 的统一 QSS；
- 页面标题、eyebrow、说明文字、metric chip、card/panel 等语义样式。

`src/apex/app.py` 的 Qt palette 与全局 stylesheet 同步，Windows、macOS、Linux 和 offscreen 测试使用同一套基础外观。

### 2. 应用壳层与导航

`src/apex/main_window.py`：

- 新增 APEX 品牌标识和 `POST-SESSION REVIEW` 模式标识；
- 导航按钮改为清晰的选中/悬停状态；
- 默认窗口调整为更适合分析界面的 1440×850，最低 1080×640；
- 参与者默认只看到当前主流程；研究模式仅增加 `Study Results`；
- 删除 `Live Pit Wall` 和 `Robot Pilot` 桌面入口。

### 3. Garage

`src/apex/garage_view.py`：

- 新增紧凑的页面标题卡；
- Session library 和当前 session 变成两个清晰面板；
- participant / phase / footage 信息进入元数据卡片，不再像文字直接贴在背景上；
- `Analyze selected lap` 成为明确主操作；
- session list、表头、行高、交替行、选中态、状态语义色全面统一；
- 保留导入、Session Debrief、右键 session 操作和双击开圈逻辑。

### 4. Lap Analysis 与折线图下方表格

`src/apex/analysis_view.py`、`src/apex/widgets/strip_stack.py`、`src/apex/coach_panel.py`：

- 标题、理论最佳、reference selector 和光标 telemetry readout 重新分层；
- 空 readout 不再显示空胶囊；
- 图表进入独立 panel，背景、坐标轴、网格、线宽和踏板面积填充重新设计；
- Track Map 继续和证据区间同步；
- AI 区域使用更紧凑、与 IBMF1 一致的 `AI Coach` 标题；
- finding cards、pattern banner、provider chip、按钮和滚动区统一视觉；
- **Corner Breakdown 改为横跨整页的底部卡片**，不再被限制在左侧窄栏；
- 表格增加 36 px 行高、交替行、弱化网格、清晰表头、单行选择与更大的可读区域；
- 保留 click 聚焦、double-click 回放、Technique review、Δ vs ref 和语义色。

### 5. Compare

`src/apex/compare_view.py`：

- 增加 BEFORE / AFTER 页面标题与解释；
- 两个圈选择器进入统一控制卡片；
- 图表增加标题和清晰图例；
- speed/reference/delta 线宽、坐标轴和网格与 Analysis 对齐；
- `What changed` 与 `Still on the table` 变成两个并列 summary cards。

### 6. 其他页面

- `Session Debrief`：页面标题卡、主操作、lap cards、间距和内容层级重做。
- `Collect Data`：guided capture 标题卡、步骤标签、主按钮/危险按钮语义统一。
- `Study Results`：标题卡、phase picker、participant table、lap progression chart 和导出操作统一；此页面继续服务论文评估。
- Audit dialog 继承新的字体、背景、边框、滚动条和文本编辑器样式。

## 删除和保留的功能边界

已删除桌面 UI 及专属 UI 测试：

- `src/apex/live_view.py`
- `tests/test_live_view.py`
- `src/apex/synthetic_capture_view.py`
- `src/apex/synthetic_capture_task.py`
- `tests/test_synthetic_capture_view.py`

特意保留：

- `racecoach` 的 live bridge / live advisor 技术实验；
- `racecoach capture-synthetic` 与底层 synthetic capture；
- 对应数据契约和非 UI 测试；
- Study Results，因为它直接用于论文的人体评估分析。

README、Granite/TORCS walkthrough、TORCS integration README、feature slides 和 slide screenshot script 已同步当前界面范围，避免后续论文材料继续声称桌面端存在已经删除的页面。

## 视觉证据

使用 Windows 原生 Qt 平台、隔离的临时 Apex workspace 和关闭的 research mode 重新生成了：

- `apex-screenshots/apex-garage.png`
- `apex-screenshots/apex-analysis.png`
- `apex-screenshots/apex-compare.png`
- `apex-screenshots/apex-audit.png`

截图只包含仓库自带 sample session，没有把本机其他 workspace session 带入仓库。截图里出现 `AI UNAVAILABLE` 仅表示截图环境未运行 Granite，不是 UI 错误。

## 验证结果

在 `C:\Users\hh25303\repos\f1-summer-project-personal` 执行：

```powershell
.\.venv\Scripts\python.exe -m ruff check .
$env:QT_QPA_PLATFORM='offscreen'
$env:APEX_RESEARCH_MODE='0'
.\.venv\Scripts\python.exe -m pytest -q
```

结果：

- Ruff：通过，`All checks passed!`
- 完整 pytest：`814 passed, 17 skipped in 78.95s`
- 针对 UI 的中间回归测试：`154 passed`
- `git diff --check`：无 whitespace error；PowerShell 只提示仓库现有的 LF/CRLF 转换策略。

新增回归约束：

- Corner Breakdown 必须保持交替行、无粗网格、36 px 行高和可见卡片容器。
- 即使开启 research mode，桌面导航也不能重新出现 Live Pit Wall 或 Robot Pilot；Study Results 必须仍可用。

## 下次继续时

1. 先读本文件，再看 `git status --short`，不要重新做本次 UI 改造。
2. 运行 `apex` 检查本机真实显示；截图基线在 `apex-screenshots/`。
3. 如只写论文，可直接使用本文件中的产品范围、IBMF1 对齐依据和验证结果。
4. 如还要微调视觉，优先改 `src/apex/theme.py` 的 token/QSS，避免在页面中重新堆硬编码样式。
5. 不要把已经删除的 Live Pit Wall / Robot Pilot 重新写进当前功能列表；底层实验只能描述为 CLI/reproducibility capability。

## 工作区注意事项

开始本次工作时仓库已经存在以下未提交状态，本次没有修改或覆盖它们：

- `docs/AI_FEEDBACK_IMPROVEMENTS.md`：原本已被删除。
- `docs/✔AI_FEEDBACK_IMPROVEMENTS.md`：原本就是未跟踪文件。

本次没有创建 commit，也没有丢弃用户已有改动。
