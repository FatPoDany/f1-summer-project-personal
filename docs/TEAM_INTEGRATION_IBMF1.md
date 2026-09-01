# Apex ↔ IBMF1 数据统一方案

*2026-08-29 · 本文档记录 Apex（个人成果）与 IBMF1（队友成果，`github.com/UOBGraduate/IBMF1`，线上 <https://demo.lzqqq.org/>）的数据打通路径。*

*状态标注规则：**[实测]** = 本机跑过并给出数字；**[读码]** = 从源码读出的契约；**[推断]** = 尚未验证的判断。*

## 0. 一句话结论

Apex 采集的原始 CSV **已经能被队友的整套服务端流水线处理**，不需要重写采集器。**[实测]**

我把 `win_collect_data/B0826-baseline-20260826-091209.zip`（未修改，71.1 MB）直接喂给 IBMF1 的
`import_torcs_bundle.py` → `build_torcs_coaching_pipeline.py` → `build_torcs_review_package.py`，
三段全部跑通，最后产出 **40 个 review event 的完整 review package**，并通过了 importer 的最终关卡
（"必须存在属于真人车手的 coaching checkpoint"）。

差的只有四件事：**14 个列名/单位适配、一个 `session.json`、一份帧索引、一次上传调用。**
四件都做完了，而且 **2026-08-31 已经真的传上去并被导入**（§4.5）：
`Custom session stored — 40 checkpoints`。

## 1. 为什么会这么顺 —— 两边本来就共用一套词汇

这不是巧合。`src/f1coach_core/torcs.py` 的模块 docstring 写得很明确：

> Adapter for Lin's TORCS high-frequency exporter (simuv2, stride 10, ~50 Hz).
> Field reference: `M_Lin/torcs_highfreq_field_cheatsheet.csv` (249 columns).

也就是说**反向早就打通了**：队友 249 列导出器的数据，Apex 一直能读、能切圈、能分析。**[读码]**
而 Apex 自己的原生记录器（`integrations/torcs-1.3.9/overlay/src/drivers/human/apex_human_telemetry_writer.cpp`）
写的 106 列，是同一套命名的**子集** —— `sim_time_s`、`race_lap`、`pos_x_m`、`track_seg_type`
这些名字两边逐字相同。

所以"数据统一"不是要发明一个新契约，而是**把这套事实上已经共享的 TORCS 宽表词汇写成正式契约**，
再补上 Apex 采不到的那几列。

## 2. 取长补短：两边各自强在哪

| 能力 | Apex | IBMF1 | 谁该是事实来源 |
|---|:--:|:--:|---|
| TORCS 真人遥测采集 | ✓ 106 列（驱动侧） | ✓ 249 列（simuv2 侧） | 各自保留，共用列名 |
| 参与者流程：同意书、问卷、设备校准 | ✓ | — | **Apex** |
| 实验阶段/分组（baseline / coached / control） | ✓ | 只有 `study_arm` 字段 | **Apex** |
| 曝光记录、审计、证据校验 | ✓ | ✓ SHA-256 evidence packet | 两边都有，需对齐 |
| 确定性规则事件检测 | ✓ | ✓ ruleset 1.0.0 + ranked opportunities | **IBMF1**（规则集更成熟） |
| 服务端 ingest、作业队列、进度回传 | — | ✓ | **IBMF1** |
| 网页 Viewer：录像 + 关键帧 + 遥测曲线联动 | — | ✓ React Viewer | **IBMF1** |
| 昵称分组、历史场次、相对上一场的提升 | — | ✓ | **IBMF1** |
| 桌面离线分析、单圈/多圈对比、报告导出 | ✓ | — | **Apex** |
| 证据约束的 LLM 反馈 | ✓ Granite / watsonx | ✓ DeepSeek streaming | 两边并存，可互为对照 |
| 参与者安装包分发 | ✓ 便携研究构建 | ✓ Windows/macOS 安装包 | 各自保留 |

结论：**Apex 管"人和实验"，IBMF1 管"服务端和展示"**，中间靠一份统一的 session bundle 连接。
这和 `docs/APEX_PARTICIPANT_WEB_ARCHITECTURE.md` 里已经写下的方向一致；本文档补的是它缺的**数据层落地细节**。

## 3. 上传契约（读自队友源码）

### 3.1 HTTP 接口 **[实测]**（2026-08-31 真实跑通）

来源：`player_kit/tools/upload_session.py`、`player_kit/config/coach-endpoint.json`

```
POST https://upload.lzqqq.org/api/import
  Content-Type: application/zip
  Content-Length: <n>
  X-Player-Upload-Token: <token>     # 见 7.2，不要硬编码进我们的仓库
  X-IBMF1-Ingest: player-app
  User-Agent: IBMF1-Player            # 必须，否则 Cloudflare 1010 直接拒
  body = ZIP 原始字节

202 → {"job_id": "..."}   然后轮询：
GET  https://upload.lzqqq.org/api/import/jobs/<job_id>
     → {"status": "...|complete|error", "message": "...", "session": {...}}

409 {"error":"duplicate"} → 这个 ZIP 传过了（按 ZIP 的 SHA-256 判重）
409 其他                  → 服务器忙，可重试（退避 4/8/12/16/24/32/40/48 秒）
401                       → token 被拒
413                       → 太大
```

要点：

- **必须发到 `upload.lzqqq.org`，不能发 `demo.lzqqq.org`。** 后者在 Cloudflare 后面，
  超过约 100 MB 的 body 会被边缘直接 413。`upload.lzqqq.org` 直连源站，上限是 nginx 的 **300 MB**。
- 判重用**整个 ZIP 的 SHA-256**（`sessions.mjs:437 findDuplicateArchive`）。改任何一个字节都会变成新场次。
- 超限时官方客户端会自动剥掉视频重传（telemetry-only）。

#### 3.1.1 两个凭据，两个头 **[实测]**

`server.mjs:653 hasUploadAccess` 认两个互不相同的头，任一通过即可：

```javascript
export function hasUploadAccess(request) {
  return validImportToken(request.headers["x-import-token"])        // IMPORT_TOKEN
    || validPlayerUploadToken(request.headers["x-player-upload-token"]);  // PLAYER_UPLOAD_TOKEN
}
```

**用 player token，不要用 import token。** 两者的定位和直觉相反，写在他们自己的
`docs/deployment/MYSERVER.md` 里：

| | `PLAYER_UPLOAD_TOKEN` | `IMPORT_TOKEN`（部署脚本里叫 `TORCS_IMPORT_TOKEN`） |
|---|---|---|
| 头 | `X-Player-Upload-Token` | `X-Import-Token` |
| 形状 | 52 字符，`ibmf` 前缀 | 64 位十六进制 |
| 存放 | `player_kit/config/coach-endpoint.json`，随每个安装包分发 | 服务器 `/etc/torcs-review-coach.env`，来自密钥库 |
| 文档原话 | 第 1210 行"**this token is not a secret**" | 第 1195 行"the **production upload credential**…**Never put values in shell history, command arguments, documentation, Git, build output, or chat**" |

本仓库的上传器只发 `X-Player-Upload-Token`，且只从 `IBMF1_UPLOAD_TOKEN` 环境变量取值。
**不要把 import token 交给它**，那是拿生产密钥做一件公开凭据就能做的事。

只读探针（查一个不存在的 job id，不产生任何写入）可以分辨两者：凭据不对回
`401 {"error":"The import access key is not valid."}`，凭据对但 job 不存在回
`404 {"error":"This import job was not found or has expired."}`。

#### 3.1.2 review 链接不是上传地址 **[实测]**

上传答复里没有 review 链接，要自己拼。**上传发 `upload.lzqqq.org`，看结果去
`demo.lzqqq.org`** —— 前者不跑站点，把司机指过去只会看到空白。队友的
`coach_config.py:100` 就是分开的：`review_url` 用 `coach_origin`，`import_url` 用 `upload_origin`。
本仓库一开始两个都用了上传域名，2026-08-31 修正为 `DEFAULT_REVIEW_ORIGIN`。

### 3.2 ZIP 内容的硬性要求 **[读码]**

来源：`deploy/coach_api/import_torcs_bundle.py`

| 约束 | 值 |
|---|---|
| 文件数 | ≤ 6000 |
| 解压后总字节 | ≤ 900 MB |
| 不允许 | 符号链接、加密条目、重复路径、`..` 越界路径 |
| 遥测 CSV | **≥ 1 个**，判定条件见下 |
| `*.frames.csv` | ≤ 1 个（可选） |
| `*.cars.csv` | ≤ 1 个（可选） |
| 视频 | ≤ 1 个（`.mp4/.mov/.mkv/.avi`，可选） |
| `session.json` | ≤ 1 个（**可选**，但强烈建议给，见 5.2） |

**一个 CSV 被认成"遥测"，当且仅当它的表头同时含有这 6 列**（`REQUIRED_TELEMETRY`）：

```
sim_time_s   race_lap   speed_body_x_mps   pos_x_m   pos_y_m   track_seg_type
```

**Apex 的采集 CSV 这 6 列全有。** **[实测]** 而且文件第一行就是表头（没有 `#` 前缀元数据行），
正好符合 `csv_columns()` 只读第一行的假设。

> 要分清 Apex 内部的两种 CSV：
>
> - **原始采集 CSV**（`human-*.csv`，106 列，`schema_version` 是一个**列**）→ 这个才是要上传的；
> - **canonical 单圈 CSV**（`t,dist,speed,…`，带 `# schema_version: 1` 注释头，见 `f1coach_core/schema.py`）
>   → 这个**不能**直接上传，第一行是 `#` 注释，6 列判定会失败。

## 4. 实测记录

环境：本机 `.venv`（pandas 3.0.5 / numpy 2.5.2）；pillow 装在 scratchpad 的 `--target` 目录，
**项目 venv 未被污染**。所有产物写在 scratchpad，未触碰研究工作区。

### 4.1 输入

`win_collect_data/B0826-baseline-20260826-091209.zip`，未做任何修改。内含 6 个成员：

```
17,429,410  human-1-1787735531-9624-1.csv     ← 真正的那次驾驶，18,833 行
     1,584  human-1-1787736065-9624-1.csv     ← 只有表头，0 行（中途退出的那次）
     1,286  manifest.json
65,703,364  session.mp4
       248  participant.json
       960  handover.json
```

### 4.2 importer 前半段 **[实测]**

```
safe_extract:          OK
识别出的遥测 CSV：     2 个（含那个 0 行的）
frames sidecar / cars：无 / 无
video：                session.mp4  ✓
session.json：         不存在 → metadata = {}
focus_run_id：         human-1-1787735531-9624-1   ✓ 选对了
```

focus 是靠 `car_name` 列匹配 `human|player` 正则选中的 —— Apex 写的是 `Player`，**碰巧命中**。
没有 `session.json` 时这是唯一的兜底路径（见 5.2）。

服务端直接从 CSV 算出的成绩摘要，与研究预设（aalborg / car7-trb1 / 3 圈）完全吻合：

```json
{ "best_lap_time_s": 126.576, "mean_completed_lap_time_s": 128.152,
  "completed_laps": 3, "finish_position": 1, "top_speed_kmh": 221.096,
  "sim_duration_s": 384.468, "distance_raced_m": 7870.442 }
```

### 4.3 coaching pipeline **[实测]**

补齐 5.1 的 14 列后，`build_torcs_coaching_pipeline.py` 正常结束：

```
rows 18833 | runs 1 | sample_rate_hz_estimate 50.0 | complete_laps 3
track_length_m_estimate 2583.813   (Aalborg)
raw_columns 120 | derived_columns_written 32
event_count 183 | opportunity_count 18
事件构成: track_edge_risk 78, corner_phase 63, braking_zone 29, load_spike 13
```

### 4.4 review package **[实测]**

```
Built review package with 40 events
events 40，全部归属 focus run；84 个文件（keyframes / event_windows）
importer 的最终关卡 "focus_events 非空" → PASS
```

这是 **telemetry-only 路径**（没传视频，因为本机没装 ffmpeg）。带视频的路径依赖服务端 ffmpeg +
VMAF/SSIM 质量检查，本机验证不了。

> ❌ **这里原本写着一条 [推断]：「视频侧应该没问题 —— 服务端对 mp4 只做 remux/重编码，不解析内容」。
> 2026-08-31 的真实上传证明它是错的。** 服务端确实不解析 mp4 的内容，但在碰 mp4 之前就先要一份
> 帧索引，没有就拒掉整个 import（见 §6 P3）。留着这条记录是因为它说明了 **[推断]** 标记的用处：
> 当时标对了，所以后来知道该去验哪一条。

### 4.5 真实上传 **[实测]**（2026-08-31）

第一次往生产服务器发，用的是 §3.1.1 那个 player token：

| 包 | 结果 |
|---|---|
| `B0826-coached`，带视频，72.8 MB | 收下、job 起来、轮询拿回错误：`Captured media needs a *.frames.csv sidecar` |
| `B0826-coached`，`--no-video`，6.2 MB | ✅ **`Custom session stored — 40 checkpoints`** |

第一行虽然失败，但它把 §3.1 从 **[读码]** 变成了 **[实测]**：鉴权、`User-Agent`、
`X-IBMF1-Ingest`、202 → 轮询 → 终态这一整条链路全部走通，失败发生在服务端的业务校验里。
第二行是**这个项目第一次把 Apex 采集的数据真的放进队友的站点**。

40 个 checkpoint 与 §4.4 本机跑出来的数量一致 —— 同一份数据、同一套流水线，
一次在本机、一次在他们服务器上，结果对得上。

## 5. 差异清单

### 5.1 列差异：14 个 **[实测]**

用"跑 → 报错 → 补 → 再跑"逐个逼出来的**完整清单**，不是猜的。

**A 类 · 纯改名（1 个）** —— 零信息损失

| IBMF1 要的 | Apex 有的 |
|---|---|
| `track_seg_width_m` | `track_width_m` |

**B 类 · 单位换算（3 个）** —— 零信息损失，除以 9.80665

| IBMF1 要的 | Apex 有的 |
|---|---|
| `accel_body_x_g` | `accel_body_x_mps2` |
| `accel_body_y_g` | `accel_body_y_mps2` |
| `accel_body_z_g` | `accel_body_z_mps2` |

**C 类 · Apex 确实采不到（10 个）**

```
fr_slip_angle_rad     fl_slip_angle_rad     rr_slip_angle_rad     rl_slip_angle_rad
fr_longitudinal_slip  fl_longitudinal_slip  rr_longitudinal_slip  rl_longitudinal_slip
total_downforce_kg    aero_drag_n
```

为什么采不到：Apex 的记录器活在 **`human` 驱动**里，只能读驱动可见的 `car->priv.wheel[]`
（`tWheelState`）和 `tCarElt`；队友的导出器活在 **simuv2 物理模块**里，能读物理内部结构。
`docs/DATA_AVAILABILITY.md` 把 slip angle 溯源到 `tWheel.sa` —— 那是 simuv2 的私有 wheel 结构，
不是驱动 ABI 的 `tWheelState`。气动力（`aero_drag_n` / `total_downforce_kg`）同理，来自 `tCar.aero`。**[推断]**

> **待确认**：`tWheelState` 是否也暴露了 `sa`。锁定的 `torcs-1.3.9.tar.bz2`（563 MiB）被 gitignore 排除，
> 本机没有，无法查证。解压后执行即可确认：
>
> ```bash
> grep -n "sa;\|slip" src/interfaces/car.h
> ```
>
> 若 `tWheelState` 有 `sa`，Apex 加 4 列就能补齐一半，成本极低。

**这 10 列缺失的实际后果**：填 NaN 后流水线**照常完成**，只是依赖它们的检测器（understeer /
oversteer index、气动相关）不会触发。实测的 183 个事件里确实没有这两类，其余四类正常产出。
**这是一个干净的降级，不是数据错误** —— 前提是不能把"没检测到"说成"没发生"（见 7.3）。

### 5.2 `session.json` 缺失的后果 **[读码]**

Apex 写的是 `manifest.json`，不是 `session.json`。importer 允许没有，但网站会失去分组能力：

| 字段 | 没有时的行为（`sessions.mjs`） |
|---|---|
| `player_id` | 分组键退化成 `id:unknown` —— **所有 Apex 上传的场次会挤进同一个匿名玩家桶** |
| `display_name` | 标题变成 `"Imported session"` |
| `session_number` / `attempt` | 序号排序失效，退回按记录时间排 |
| `recorded_at` 等 | 网站按 ZIP 内的记录时间排序，缺了只能按上传时间 |
| `focus_run_id` | 退到 `car_name` 正则兜底（目前碰巧能中） |

`ibmf1-player-session-v2` 的完整字段见 `player_kit/tools/pack_session.py:562-601`。
和 Apex 现有数据的对应关系 —— **这是最省事的部分，几乎全是现成的**：

| session.json | Apex 来源 |
|---|---|
| `player_id` / `display_name` | `participant.json.participant_id`（假名，见 7.1） |
| `attempt_label` / `study_arm` | `manifest.json.phase`（`baseline` / `coached` / `control`） |
| `session_number` | 同一 participant 的第几次采集，Apex run store 里有 |
| `started_at` / `finished_at` | `manifest.json.started_at` / `finished_at`（已是 UTC ISO） |
| `track` / `vehicle` | `manifest.json.study_preset.track_id` / `car_id` |
| `run_id` / `focus_run_id` | `manifest.json.runs[0].run_id` |
| `telemetry_sha256` | `manifest.json.runs[0].sha256`（**已经在算了**） |
| `sample_rate_hz` | 50 |
| `controller` | `"human"` |
| `video_file` | `session.mp4` |

### 5.3 其他两个小问题 **[实测]**

1. **0 行的第二个 CSV 会被当成第二个 run 收进去。** 实测流水线能吸收掉它
   （最终 `runs: 1`，lap summary 里只有真实那条），所以不是阻断问题；但它会白白进服务端的
   `source_data.zip`，理论上还可能被 `focus_run_id` 的兜底逻辑选中。打包时应该滤掉零行 CSV。
2. **没有 `*.frames.csv`，录像与遥测的对齐会退化。** IBMF1 用这个 sidecar 把视频帧对到 sim_time；
   没有它，Viewer 只能按恒定 fps 假设推算。我们这边 `docs/✔REPLAY_SYNC_POSITION_VS_TIME.md`
   正好研究过同一个问题，两边应该统一到同一种对齐方式。

## 6. 落地方案

### P0 — 导出适配器 ✅ 已完成（2026-08-29）

在 Apex 侧加了"导出为 IBMF1 bundle"的能力，**没改采集器、没改队友的服务端**。

- **`src/f1coach_core/ibmf1.py`**（新增，纯数据、无 Qt）
  - `to_ibmf1_columns(df)`：A 类改名 + B 类换算 + C 类补空列。**已有的列绝不覆盖** ——
    将来采集器真的开始写某一列，测量值优先于翻译值。
  - `study_arm_for(phase)`：见下面那条方框，这是全模块最要紧的一个函数。
  - `build_session(...)`：按 5.2 的映射生成 `ibmf1-player-session-v2`。
  - `package(capture_dir, destination, *, session_number, include_background, include_video)`：
    滤掉零行 CSV，带上 `session.mp4` 和问卷，写 `session.json`。
- **`src/racecoach/telemetry/ibmf1_upload.py`**（新增）：§3.1 的 POST + 轮询 + 退避重试。
  **token 只从 `IBMF1_UPLOAD_TOKEN` 环境变量读，仓库里没有任何位置写过它**；没设就报错并说明怎么设，
  而不是发一次匿名请求再拿 401。
  轮询**能扛住本机网络抖动**（2026-08-31 补）：一次没到达服务器的请求不是关于 job 的回答——
  import 在他们机器上跑，这边看不看得见都一样。实测过一次 76 MB 的包被收下、job 起来了，
  客户端却因为一次 `getaddrinfo failed` 报了失败。现在连续 10 次不通才放弃，
  而且放弃时说的是"**可能已经成功了，去站点看**"，不是"失败了"。
- **CLI**：`racecoach export-ibmf1 <capture_dir> [--out] [--session-number] [--no-background] [--no-video]`
  和 `racecoach upload-ibmf1 <archive>`。
- **`frame_index()` / `session.frames.csv`**（2026-08-31 补）：带录像时必须同行的帧索引，见 P3。
- **`DEFAULT_REVIEW_ORIGIN`**（2026-08-31 补）：看结果的域名和上传的域名不是一个，见 §3.1.2。

放在 `f1coach_core` 是因为 AGENTS.md 规定它拥有遥测契约；上传是网络行为，归 `racecoach`。

> **C 类 10 列：定为写空列，不是不写。**
> 理由不是省事，而是"空"本身就是唯一诚实的值：pandas 读成 NaN，队友流水线在这些列上做的
> 每个 quantile/mean 都会跳过它，所以依赖它们的检测器**不产出**，而不是从一个没人测过的数产出。
> 写 0 才是发明通道。列名和原因写在 `UNAVAILABLE_CHANNELS` 的注释里，
> 并且每个包的 `session.json` 都带 `apex_empty_columns` 明说哪些是占位。
> 干净的解法仍在上游（P2 第 1 条）；那边落地后这个列表可以缩到空。

> ⚠️ **`study_arm_for()` 是这次改动里唯一会伤到实验的函数。**
> 只有 `phase == "coached"` 才声明成可辅导；`baseline`、`control` 以及将来任何新 phase
> 都原样送过去。这**正好**咬合我提的 PR #15 —— 它对"不认识的组名"一律拦下。
> 没有 phase 的采集会显式声明 `unknown` 而**不是**不声明：不声明恰恰是队友服务端读成"可以辅导"的那个值。
> 方向错在安全那侧只是让参与者少看一次复盘；错在另一侧就是揭盲，实验当场作废。
> CLI 每次导出都会把这句话打出来。

**送出去的和不送的**：送遥测 CSV（翻译过）、`session.mp4`、`participant.json`、`session.json`。
**不送** `manifest.json`（里面是本机盘符和用户名，而且它的摘要描述的是翻译前的文件）、
`handover.json`（同样的摘要理由）、`exposure.jsonl`（参与者读了多少辅导，是本研究自己的测量，
队友服务端也用不上）。翻译改变了文件字节，所以 `session.json` 同时带
`telemetry_sha256`（**送出去那份**）和 `apex_source_sha256`（**本工作区测量的那份**），两者可以对上。

#### P0 的验证

| 检查 | 结果 |
|---|---|
| `tests/test_ibmf1.py` | **31 passed** |
| `ruff check .`（CI 的命令，整仓库） | All checks passed |
| `QT_QPA_PLATFORM=offscreen pytest -q` | **858 passed, 17 skipped** |
| **端到端：适配器产出的包 → 队友真实流水线** | importer 认出遥测和视频 → coaching pipeline **无任何手工补列直接跑完** → review package **40 events，importer 终关卡 PASS** |
| 在 PR 分支上验新函数 | `study_arm('baseline')` → **WITHHOLD**；`participant_profile()` 读出完整问卷 |
| CLI 运行时 | baseline 打印"will withhold"；coached 打印"WILL offer"；`--no-video` 68.3 MiB → 5.9 MiB；无 token 时报错并退出 1 |

端到端用的是真实数据：`win_collect_data/B0826-baseline-20260826-091209.zip` 解包后当作采集目录，
产出 68.3 MiB 的包，里面只有那条 18833 行的真实驾驶（**空的那条被滤掉了**）。

### P1 — Apex 直接写 `session.json`

把 5.2 的映射前移到采集结束时，在 `manifest.json` 旁边直接落一份 `session.json`。
这样"导出"退化成纯打包，也方便手动上传。

### P2 — 上游对齐（需要和队友谈）

1. 请队友给 C 类 10 列加缺失保护：缺失即跳过对应检测器，而不是 KeyError。
2. 把 `track_seg_width_m` / `track_width_m` 的命名分歧定下来，写进一份共享的字段字典。
3. 确认 `tWheelState.sa` 是否可用；可用的话 Apex 记录器补 4 列 slip angle。

### P3 — 录像对齐 ✅ 已完成（2026-08-31）

**触发它的是一次真实上传的失败。** 带视频的包传上去，服务端 job 起来了然后报
`Captured media needs a *.frames.csv sidecar`（`import_torcs_bundle.py:489`）：
`prepare_media` 只要发现有视频或图片而没有帧索引，就拒掉整个 import。

读下来发现这条比原先估的便宜得多 **[读码]**：

- `import_torcs_bundle.py:541` —— 包里没有 PNG 时，**服务端自己用 ffmpeg 从视频里按
  `video_fps` 抽帧**，命名 `torcs-0001-%08d.png`，`-start_number 0`。所以 Apex **不需要**
  输出 PNG 序列，只要给出一份索引。
- `prepare_media` 对索引的硬要求只有两条：非空、有 `frame_id` 列。
- `build_torcs_review_package.py:1465` 定义 `frame_id` 是"**Zero-based frame number**"，
  `sim_time_s` 用来把复盘事件配到最近的一帧。

于是 `f1coach_core.ibmf1.frame_index()` 写出 `session.frames.csv`：

| 列 | 来源 |
|---|---|
| `frame_id` | 服务端 ffmpeg 会给这一帧的序号，`int(duration_s * fps)` 之内 |
| `sim_time_s` | 由 wall clock 反查出来（见下） |
| `image_file` | `torcs-0001-{frame_id:08d}.png`，与服务端命名一致 |
| `capture_id` / `display_width` / `display_height` | focus run id / manifest 的 `study_preset` |

不写 `re_cur_time_s`：那是 TORCS 自己的引擎时钟，模拟器外面读不到，宁可缺列也不拿另一个时间冒充。

> ⚠️ **不能假设 sim_time 就是视频时间。`B0826-coached` 这场差了 828 秒。**
> wall clock 跨 **1233.1 s**，而 `sim_time_s` 只跑到 **405.1 s**。
> 如果按"第 n 帧 = sim_time n/fps"去对，最后一帧会错 800 多秒——整场复盘的画面全是错的角落。
>
> **更正（2026-08-31）**：本文档此前把这 828 秒记成"机器跟不上 TORCS，模拟时间慢了三倍"。
> **那是错的**，逐圈量过：这台机器跑 TORCS 是 **1.000x 实时**。828 秒全部来自**一次暂停**
> ——参与者在第 2 圈按了 Esc，停在 `Race Stopped` 菜单上 13 分 48 秒，
> 期间 `sim_time_s` 只前进了 0.020 s（一个模拟步），而录像一直在录那张静止的菜单。
> 见 §6 P4。
> 对齐必须走 `wall_clock_s`：录像知道自己第一帧的 wall clock（`manifest.recording.started_at`），
> 每个遥测样本带着自己的 wall clock，两者一插值就得到帧的模拟时间。
> `f1coach_core/footage.py` 早就是这么做的，常量也从那里取，不在这里重名一次。

**遥测覆盖不到的帧直接不写，而不是钉到首尾。** 录像比 TORCS 早开、比它晚停，
把开头那 34 帧说成"发生在第一个采样的时刻"是没人测过的数；而且他们的 builder 是按
`sim_time_s` 最近来挑帧的，这种行会去争着当开场和收尾事件的配图。`frame_id` 保持
ffmpeg 会给的真实序号，所以少写几行不会挪动剩下的行。

`session.json` 同时写 `video_fps`，**必须和索引的行频一致**（服务端按它抽帧）。
没有录像时**不写这个键**——他们读的是 `float(metadata.get("video_fps", 10.0))`，
键在而值为 null 会变成 TypeError 而不是取默认值。

> **取 `SIDECAR_FPS = 4.0`，不是他们的默认值 10，理由是他们自己的 `MAX_FILES = 6000`。**
> 他们的采集把 PNG 装在包里传，所以这个上限同时也是**他们的流水线见过的单场最大帧数**。
> Apex 传的是录像、由服务端自己切图，不受 `MAX_FILES` 约束——按 10 fps，一场二十分钟的
> 比赛会让他们切出约 12500 张，是他们跑过的任何东西的两倍。4 fps 让同样长度的比赛落在
> 他们自己的信封里（4994 张）。丢掉的是没人用的分辨率：复盘每个事件只取一帧、总共约 40 帧，
> 而 0.25 秒的间隔对一场 405 秒的比赛来说足够细。

**验证 [实测]**：真实 `B0826-coached` 导出 **4932 行**索引，`frame_id` 14–4945 连续，
首尾两行的 `sim_time_s` 用独立重算对上（0.2429 / 404.8821），最大 `frame_id` 小于
ffmpeg 会切出的 4994 帧，而 4994 < `MAX_FILES` 6000。8 个新单元测试覆盖插值、
越界剔除、`video_fps` 的有无、以及两种拒绝路径，断言全部跟着 `SIDECAR_FPS` 走，
改帧率不用重写测试。

**拿他们的 importer 在本地跑我们的包 [实测]**：

```
find_inputs   -> telemetry ['human-1-...csv']  sidecar session.frames.csv
                 video session.mp4  video_fps 4.0  (1 <= fps <= 60 ✓)
prepare_media -> torcs-0001.frames.csv，4932 行，6 列俱全
                 指向不存在的帧文件的行数：0
```

`session.frames.csv` **没有**被他们的过滤器当成第二条遥测（`find_inputs` 明确排掉
`.frames.csv`）。上一次挡下整个 import 的那个函数这次跑完了。

**仍未实测的一环**：上面这次本地跑，把他们的 `optimize_mp4`（重编码 + VMAF）和抽帧
两处 ffmpeg 调用打了桩——这台机器上没有 ffmpeg，只有打包进研究版的那份。抽帧的桩是
按他们真正发出的命令建的文件：

```
ffmpeg -i session.mp4 -vf fps=4 -start_number 0 torcs-0001-%08d.png
```

所以"0 行指向不存在的文件"成立的前提是这条命令的输出建模正确。**没证的只剩 ffmpeg 自己的行为。**

### P4 — 剪掉暂停 ✅ 已完成（2026-08-31）

**用户看队友网站上的视频时发现的**：传上去的录像里有一大段游戏暂停界面，整个视频被拉长。

**量出来的**（`sim_time_s` 与 `wall_clock_s` 逐样本比对，5 场 handover 全扫）：

| 采集 | 录像时长 | 比赛时长 | 暂停次数 | 死时间 | 占录像 |
|---|---|---|---|---|---|
| `0823-baseline` | 382.1 s | 364.5 s | 0 | 0 s | 0.0% |
| `B0826-baseline` | 540.8 s | 384.5 s | 2 | 139.4 s | **25.8%** |
| `B0826-coached` | 1248.5 s | 405.1 s | 1 | 828.1 s | **66.3%** |
| `C0826-baseline` | 742.0 s | 354.9 s | 1 | 369.1 s | **49.7%** |
| `C0826-control` | 381.4 s | 361.0 s | 0 | 0 s | 0.0% |

**五场里三场有暂停，不是个例。** 已经传上服务器的那场（`B0826-coached`）是最严重的一场：
参与者在第 2 圈按 Esc 停在 `Race Stopped` 菜单上 **13 分 48 秒**，`sim_time_s` 期间只走了
0.020 s。这同时造成两个后果：

1. **视频**：2/3 的时长是一张静止的菜单截图。
2. **帧索引**：4932 行里 **3312 行（67%）**挤在 0.02 秒的模拟时间窗口里。他们的 review
   builder 是按 `sim_time_s` 最近挑帧的，所以模拟时间 158.8 s 附近的任何 coaching 事件，
   配图都会是暂停菜单。

**怎么判定是暂停而不是机器慢**（`f1coach_core/footage.py: stalls`）：看的是**死时间**
`Δwall − Δsim` 而不是 `Δwall` 本身。录像器每个模拟步都写一行遥测，所以机器慢只会产生
大量小间隔，永远不会在两个**相邻样本**之间产生秒级的空档；只有模拟器停了才会。
阈值 2.0 s：对这 5 场采集，它抓到全部暂停、放过全部卡顿（最大的一次卡顿 0.6 s）。

**怎么剪**（`racecoach/telemetry/screen_capture.py: condense`）：`trim` + `concat` 滤镜图，
重编码而不是 `-c copy`——流拷贝只能在关键帧上切，会留下最多一整个 GOP 的菜单，
更糟的是会让剪口之后的每一帧都和索引声称的时间对不上。

**帧索引跟着改**（`f1coach_core/footage.py: Segment` / `segments_of`）：剪过之后
"文件时间"不再等于"录像开始以来的时间"。`segments_of` 给出文件里每一段的
`(在文件中的位置, 那一刻的 wall clock, 时长)`，`frame_index` 用它把每帧还原回墙钟，
再插值出模拟时间。**不剪的比赛走的是同一条路径**（一段覆盖全片），代码没有分叉。

**采集原件不动。** 剪的是为上传做的副本（临时目录），磁盘上的 `session.mp4` 保持原样。

**实测（真实 `B0826-coached` 重新导出）**：

```
recording: included, 1620 frames indexed
paused mid-race: 828 s of menu cut out before packing
```

| | 修复前 | 修复后 |
|---|---|---|
| 视频时长 | 1248.2 s | **420.1 s** |
| 视频体积 | 70.9 MB | 58.4 MB |
| 包体积 | 72.8 MiB | **61.9 MiB** |
| 索引行数 | 4932 | **1620** |
| 挤在冻结瞬间 ±0.05 s 的行 | **3312** | **0** |
| 行间模拟时间间隔（中位） | — | 0.2494 s（= 1/4 fps ✓） |
| 服务端要抽的帧数 | 4994 | **1681** |

本地剪一次约 **30 秒** CPU（1280×720、21 分钟、`-preset veryfast`）。

**这条同时把两个一直"读码未实测"的点关掉了 [实测]**，因为研究版里那份 ffmpeg
（`Apex/ffmpeg/ffmpeg.exe`）就能用：

- 他们的 `ffmpeg -i session.mp4 -vf fps=4 -start_number 0 torcs-0001-%08d.png` 对剪后的
  视频**实际**产出 **1681 帧**，命名从 `torcs-0001-00000000.png` 起——和索引里
  `image_file` 写的完全一致，最大 `frame_id` 1633 < 1681，没有一行指向不存在的文件。
- **独立交叉验证**：索引说 `frame_id 1600` 是模拟时间 396.702 s，遥测在那一刻是
  第 3 圈、46 km/h；把那一帧抽出来看，HUD 显示 `Laps: 3 / 3`、码表 `46`。

**注意：原片是可变帧率。** gdigrab 名义 30 fps，实际 33172 帧 / 1248.2 s = **26.58 fps**。
所以剪切**不能**用 `setpts=N/FRAME_RATE/TB` 重打时间戳——那会把丢帧的段落加速播放
（第一次试的结果是 345 s 而不是 420 s）。`trim`+`concat` 保留原始 PTS 间距。

**测试**：新增 18 条（footage 7、screen_capture 5、ibmf1 6），全套 **887 passed**。
两条关键断言在关掉修复后确实变红：索引行间隔塌成 0.0001 s，以及索引指向文件里不存在的帧。

**未做**：录像头尾（进入比赛前的菜单、冲线后的结算画面）没有剪，只剪比赛中的暂停。

---

## 7. 三个决定 —— 已定（2026-08-29）

> 三条都由用户拍板了，结论写在每节开头。7.1 和 7.3 已经写成给队友仓库的两个 PR，见 §10。

### 7.1 参与者数据能不能上传到队友的服务器

> **已定：保留问卷数据，并把服务端这个缺陷补上。** 用户认为有参与者问卷会更好，
> 否决了我"上传前剔除"的建议。对应 PR：`carry-participant-questionnaire`。

`participant.json` 含 `age_band`、驾驶经验、模拟驾驶经验。虽然是假名（`B0826`），
但这是**人体研究数据流向第三方主机**。importer 不解析它，但会把它原样收进服务端的
`source_data.zip` 长期保存。

选项：(a) 上传前剔除 `participant.json`；(b) 只上传遥测和视频，问卷留本地；(c) 确认现有同意书已覆盖。
**建议 (a)** —— 网站根本不需要这些字段，去掉零成本。

### 7.2 上传 token

> **已定：接受现状。** 仓库目前私有，只有团队成员能访问，风险边界比我原先估计的小得多。
> 我们这边仍然从环境变量 / keyring 读，不把 token 写进本仓库任何位置。

队友仓库的 `player_kit/config/coach-endpoint.json` 里**明文提交了一个 upload token**。
我没有把它复制到本仓库的任何位置，本文档也不记录它的值。

两件事：(1) 我们这边一律从环境变量 / keyring 读，不进 git；(2) 建议提醒队友把它挪出仓库并轮换 ——
现在任何能读到那个仓库的人都能往生产 ingest 接口投递数据。

### 7.3 ⚠️ 会破坏实验设计的风险

> **已定：提 PR 修掉。** 对应 PR：`withhold-coaching-from-control-arm`。
> 读了队友的 `CLAUDE.md` 之后，这条比我原先写的更严重也更具体 —— 见下面的补充。

**补充（读码后修正）：队友其实已经设计了对照组，但闸门只在客户端。**
`CLAUDE.md` 的 "Study arms" 一节写着：`IBMF1_COACHING_ENABLED=false` 构建出对照组安装包，
比赛照常打包上传（两组必须产出可比的遥测），**只是启动器跑完不打开 review**。
但 `025c2b6` 之后任何人不用 key 就能从 Sessions 列表打开任何一场比赛，而 `/coach/stream`
完全不知道"实验分组"这回事 —— 对照组参与者只要从网站找到自己那场，就能拿到 AI 辅导，
事后数据里也看不出来。**这是他们自己实验设计里的漏洞，不只是 Apex 接入的问题。**

`coach-endpoint.json` 里 `coaching_enabled: true` —— **网站导入后会自动跑 AI coaching 并展示。**

如果把 **control 组或 baseline 阶段**的场次传上去，参与者一打开网站就会看到辅导内容，
**正式对照实验的分组盲法当场失效**。这和 `docs/APEX_PARTICIPANT_WEB_ARCHITECTURE.md` 里
已经写下的那条约束是同一件事："未接受辅导组在主要终点完成前必须被实验权限隐藏反馈。"

所以上传必须按 `phase` 分流，或者等这批数据收完再传。**这个决定权在你，不是技术默认值。**

## 8. 复现本文档的实测

```bash
# 1) importer 前半段 + 成绩摘要
python - <<'PY'
import sys; sys.path.insert(0, ".../IBMF1/deploy/coach_api")
import import_torcs_bundle as imp
imp.safe_extract(ARCHIVE, EXTRACTED)
telemetry, sidecar, cars, video, metadata = imp.find_inputs(EXTRACTED)
staged = imp.stage_telemetry(telemetry, STAGED, metadata)
print(imp.focus_run_id(metadata, staged), imp.telemetry_performance(staged[0][1]))
PY

# 2) 补 14 列后跑 coaching pipeline（LIGHTWEIGHT=1 跳过 matplotlib）
TORCS_TELEMETRY_DIR=... TORCS_COACHING_DIR=... TORCS_PIPELINE_WORK_DIR=... \
TORCS_PIPELINE_LIGHTWEIGHT=1 MPLBACKEND=Agg \
  python IBMF1/scripts/build_torcs_coaching_pipeline.py

# 3) review package（需要 pillow）
python IBMF1/scripts/build_torcs_review_package.py --output-dir ... \
  --video-run-id human-1-1787735531-9624-1 --video-fps 10.0 --run-metadata ...
```

## 9. 给队友仓库的两个 PR（2026-08-29，已提交）

- **PR #15 — Keep the control arm out of the coach** ← <https://github.com/UOBGraduate/IBMF1/pull/15>
- **PR #16 — Keep the questionnaire that came with a race** ← <https://github.com/UOBGraduate/IBMF1/pull/16>

两个都基于 `main`（`bf55b0d`），互相独立，各 1 个 commit。

### 9.1 `withhold-coaching-from-control-arm` — PR #15，commit `43aa0ab`

把对照组的闸门从客户端搬到服务端。

- `import_torcs_bundle.py`：`session.json` 里本来就有 `study_arm`（`session_context.py`
  归一成 `coached`/`control`，`pack_session.py` 写进去），importer 读了旁边每一个字段
  **偏偏漏掉这个**。现在连同 `attempt_label` 一起记进 `import_summary.json`。
- `sessions.mjs`：新增 `coachingAllowed()`。只有"声明是 coached"或"根本没声明"
  （手动网页上传、内置 demo）才放行；**任何其他声明的分组一律拦下**，包括这个构建不认识的
  ——不认识的组名不构成"可以看辅导"的证据。
- `server.mjs`：`/coach/stream` 在查 checkpoint **之前**就返回 403。
- **文案刻意不提分组**：403 只说 "AI coaching is not available for this race"，
  且 `study_arm` 完全不进 `publicSessionView`。告诉参与者他在哪一组、或者把每场比赛的
  分组公开给所有访客，等于用这道闸门本来要保护的那个页面把实验揭盲了。
- 分组存在 `record_json` 里，不需要改表结构；有测试专门验它能挺过 SQLite 往返
  —— 一道重启后悄悄失效的闸门比没有闸门更糟。
- **已知限制（PR 里写明了）**：这次改动之前导入的比赛没有分组字段，仍然可辅导，
  而且在存储数据里和手动上传无法区分。

### 9.2 `carry-participant-questionnaire` — PR #16，commit `6e6480e`

让服务端真正认识 Apex 的 `participant.json`（`apex-participant-v1`）。

- 之前它只是躺在 `source_data.zip` 里，不手动解包就读不到 ——
  **唯一说明"谁开的这场"的东西，恰恰是 session 目录唯一答不上来的东西。**
- `participant_profile()`：每个包最多一个，六个短字段 + 一个校验过的时间戳，
  逐个按他们既有的方式限长。不认识的键直接丢掉，包里塞不进任意内容；
  完全没有问卷、或只有不认识的字段，就存 null 而不是一行空值。
- **不进 `publicSessionView`。** 那个视图产出的每个响应都不需要 key，
  年龄段 + 驾驶经验 + 昵称 + 圈速放在一起就不再是"比赛数据"而是"参与者数据"。
  它留在存储记录里，研究者本来就有访问权。带 key 的研究者视图是自然的下一步，
  这次刻意不做。

### 9.3 验证情况

| 门 | 结果 |
|---|---|
| `python -m unittest discover -s tests`（他们的 Python 门） | **155 passed, 7 skipped** —— rebase 到 `bf55b0d` 后两个分支都是 |
| `node --test sessions.test.mjs server.test.mjs` | `withhold-…` **38 pass / 0 fail**；`carry-…` **37 pass / 0 fail**（rebase 后一次绿） |
| **他们自己的 CI**（ubuntu + Node 20，PR 触发） | **#15 success，#16 success** —— 这才是权威平台 |

两个 PR 都是 `mergeable: clean`：#15 +141/-3（6 个文件），#16 +139/-0（4 个文件）。
| 新增测试单独重跑 | 对照组 5/5 绿；问卷 3 次里 2 次绿（第 3 次是下面那个抖动） |
| `participant_profile()` 对真实 Apex 数据 | 四种情况全对（真实问卷 / 无文件 / 只有未知字段 / 两个文件报错） |

为此在本机装了 Node（**v24.20.0 + npm 11.19.0**，官方 zip，SHA-256 对过 nodejs.org 发布值，
装在 `D:\apex-build\toolchain\nodejs`）。

**一个必须记下来的坑**：他们的 JS 套件在 Windows 上本来就抖 —— 大约每两次就有一次，
某个测试在 `persistImportedSession` 的目录 rename 上挂 `EPERM`，每次挂的还不是同一个。
**干净的 `main` 一样复现**（三次跑分别是 3 个失败、0、0），把 `TMP` 挪到 D: 也没用。
是 Windows 目录重命名/杀软的问题，不是代码。他们 CI 是 ubuntu + Node 20，碰不到。
**看到红的先重跑，别当成回归。**

### 9.4 权限（已解决）

一开始推不上去：`FatPoDany` 对 `UOBGraduate/IBMF1` 只有 `pull`，而且仓库私有 +
`allow_forking: false`，连 fork-PR 路线都不存在。用户找人开了写权限（现在
`{pull: true, push: true, triage: true}`），两个分支已推送、PR 已提交。

**如果以后又推不上去，先查权限，不要当成凭据问题** —— token 的 `repo`/`workflow`
scope 一直是好的。

补丁仍留在 `C:\Users\hh25303\repos\ibmf1-patches\` 作为备份，但已经过时（那是 rebase 前的版本）。

### 9.5 两个 PR 的相互影响

它们互相独立，但改到了三个文件的相邻位置：`CHANGELOG.md`、`import_torcs_bundle.py`、
`sessions.test.mjs`。**先合哪个都行**，后合的那个需要小 rebase，没有逻辑冲突。

## 10. 未完成 / 下一步

- [x] ~~7.1 / 7.3 提 PR~~ —— **#15、#16 已提交，CI 全绿，等队友 review**
- [x] ~~P0 适配器实现 + 测试~~ —— 见 §6 P0，端到端跑通
- [x] ~~真实上传到 demo.lzqqq.org~~ —— 见 §4.5，telemetry-only 已 stored，40 checkpoints
- [x] ~~P3 帧索引~~ —— 见 §6 P3，866 passed
- [x] ~~带视频的包真实上传一次~~ —— 2026-08-31 `complete 100% :: Custom session stored`，
      40 checkpoints（与 telemetry-only 那次一致），服务端耗时约 50 分钟、存 118 MB
- [x] ~~`frame_id` ↔ `torcs-0001-%08d.png` 自己抽帧核对~~ —— 见 §6 P4，用研究版自带的
      `Apex/ffmpeg/ffmpeg.exe` 实测：1681 帧、`torcs-0001-00000000.png` 起，命名与数量都对上
- [x] ~~剪掉录像里的暂停~~ —— 见 §6 P4，5 场里 3 场受影响
- [ ] **把已经传上去的那场重传** —— 服务器上现在那份仍然是 1248 秒、2/3 是暂停菜单的版本，
      索引里 3312 行指着冻结画面。重传要么会撞 409（ZIP SHA-256 变了，应该不会），
      要么会多出一场，需要和队友说一声删掉旧的
- [ ] 另外两场有暂停的采集（`B0826-baseline` 25.8%、`C0826-baseline` 49.7%）尚未导出上传
- [ ] 确认 `tWheelState.sa` 是否可用（需要解压锁定的 TORCS 归档）
- [ ] 和队友对齐 P2 的三条上游改动
- [ ] 建议队友轮换 `IMPORT_TOKEN`：2026-08-31 它被贴进过对话和本机 shell 历史，
      按 `MYSERVER.md:1201` 的要求这两处都不该出现它
