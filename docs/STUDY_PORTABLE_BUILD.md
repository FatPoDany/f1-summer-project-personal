# 便携研究版打包记录（2026-08-26 首次；2026-08-27 因辅导曝光量重打，见第 3 节）

> **English summary — *The portable study build that was handed to
> participants.*** A working note in the language it was thought in, recording the
> first package on 2026-08-26 and the rebuild on 2026-08-27 once coaching exposure
> was being measured.
>
> The build is install-free and needs no administrator: copy it to another Windows
> machine, unzip, double-click `Apex.exe` and collect data. Sessions are written to
> `C:\Users\<user>\Apex\` on that machine and come back through the
> application's own *Save a file to send*, as a zip with a checksum. Section 2
> pins down exactly which source the executable contains, because packaging must
> not mean copying something that merely looks current. Section 4 explains why a
> portable folder rather than an installer, section 5 what to do on the other
> machine, section 6 the four study phases and the discipline that keeps the arms
> apart, and section 7 how the data comes back.
>
> Section 8 is why the 2.1 GB model weights are not inside the package and how the
> machine gets them; section 9 is what was deliberately left out; section 11 is a
> packaging trap worth remembering; and section 12 records the 2026-08-28 rebuild.

## 1. 一句话

把当前 HEAD 打成了一个**免安装、免管理员**的便携包：拷到另一台 Windows 机器、解压、双击
`Apex.exe` 就能采数据。数据写在那台机器的 `C:\Users\<用户>\Apex\`，采完用软件里的
「Save a file to send」导出成一个带校验和的 zip 交回来。

---

## 2. 这份包里装的是哪份代码

打包不是拷贝一个"看起来像最新"的 exe，所以先把来源钉死：

| 项 | 值 |
|---|---|
| 源 commit | `fab9db59a7bef16518bd653566cf2d2c1772716f`（"Add Session Debrief View and associated tests"） |
| 工作树 | 打包时 `git status` 干净，0 个未提交文件 |
| 全量测试 | **656 passed, 17 skipped**（offscreen，本次打包前跑的） |
| 运行时 | Python 3.13.15 / PySide6 6.11.2 |
| 构建方式 | `pyinstaller apex.spec`，全新构建，不是在旧 dist 上增量 |

**验证 exe 里确实是这份代码**，而不是相信文件时间戳：把 `Apex.exe` 里附加的 PYZ 用 zlib
逐段解压，grep 只有新代码才有的标识符。

    SessionDebriefView       YES     ← Debrief 视图（fab9db5）
    _DebriefTask             YES
    identity_line            YES
    _coach_blocked_reason    YES
    _offer_phases            YES     ← 6.3 的 phase 选择器
    Against this phase       YES

顺带发现一件事：`C:\Users\hh25303\repos\Apex` 里那份 20:17 的 exe **已经**包含 Debrief 视图
了，虽然那个 commit 是 20:27 才提交的 —— 你当时是先打包、后提交。所以旧包并不算过期。但这次
是从干净 HEAD 重新构建的，来源可追，研究仪器该这样。

---

## 3. 成品在哪

| 路径 | 是什么 | 大小 |
|---|---|---|
| `D:\apex-dist\Apex-Study\` | 解开的目录，可以直接拷 U 盘 | 1002 MB |
| `D:\apex-dist\Apex-Study-2026-08-27.zip` | 打好的 zip | **576.7 MB** |
| `C:\Users\hh25303\Apex-Study-2026-08-27.zip` | **同一个 zip 的副本，放在 profile 里** | 576.7 MB |
| 两处各有一个 `.zip.sha256` | 拷贝之后核对用 |  |

    SHA-256  03943F7A3CCFC85F6A98A1628729E4DC1B9A55517FBFA3D2FAE51BE374A570E3

到了另一台机器上核对：

    certutil -hashfile Apex-Study-2026-08-27.zip SHA256

> **2026-08-27 重打了一次，08-26 那个 zip 已经删掉。**这一版必须换：辅导曝光量
> （`docs/STUDY_CAN_WE_SHOW_IMPROVEMENT.md` §11）是**在参与者那台机器上记录的** ——
> review 窗口开了哪个弯、停留多久，随 `exposure.jsonl` 一起进交回来的包。旧 zip
> 不记这些，用它采回来的数据剂量列会是空白。上一次（§10）改的全在研究者这一侧，
> 所以当时没重打；这一次不一样。
>
> 顺带补上了两个 08-26 早上那版漏掉的文件：`_internal\libcrypto-3-x64.dll` 和
> `libssl-3-x64.dll`（1072 → 1074 个文件）。少了它们，需要 HTTPS 的路径（例如让 app
> 自己下载 Granite 权重）会失败。
>
> **同一天打了两次，文件名没变，上面的 SHA-256 是第二次的。**第一次（约 09:55，
> `A9C5FA5C…40FB`）已经被覆盖。第二次多了三样：研究者写的 advice adherence（`racecoach
> study-adherence` + summary 里五列）、`utf-8-sig` 那个修正（见下），以及一次完整的
> 启动检查。**如果你在 09:55 到 11:00 之间已经把 zip 拷到过 U 盘，那份是旧的** ——
> 用 `certutil -hashfile` 对一下上面的哈希，或者看解压出来的 `apex-study-build.txt`，
> 第二次那份第一行写的是 "second swap of the day"。
>
> **2026-08-27 12:15 又打了第三次**（上面的哈希是第三次的）。这次换的是
> **arrived 标记**：从 handover 收进来的 session 现在带一个 `arrived.json`，这种
> session 的浏览**永远不记成剂量**。没有它的话，研究者在分析机上点参与者的圈，会被记
> 成"参与者在读自己的辅导" —— 这件事**已经发生过**，真实 workspace 里查出 16 条假记录
> （见 `STUDY_CAN_WE_SHOW_IMPROVEMENT.md` §11.10）。同一天三次打包的区别：09:55 只有
> 曝光量；11:00 加 adherence + `utf-8-sig`；12:15 加 arrived 标记。**认哈希，或者看
> 解压出来的 `apex-study-build.txt` 第一行写的是第几次。**
>
> **`utf-8-sig`**：从别人机器上过来的文件（`manifest.json` / `participant.json` /
> `exposure.jsonl`）以前是按 utf-8 读的。Windows 上任何东西重写过这些文件都可能在开头
> 留下三个字节的 BOM，按 utf-8 读就会**静悄悄地丢掉第一行** —— 对 manifest 来说，那等于
> 整个 handover 变成"没有身份的圈"。这是**跑打好的 exe 时才发现的**：写了 3 条 view，
> 收进去只有 2 条。

解压后是一个 `Apex-Study\` 目录（不会把 3884 个文件散到当前目录里）。

**为什么要放两份**：`D:` 是这台会话主机的本地盘。这是 AVD 主机池，重连有可能落到另一台机器
上，那台的 `D:` 里没有你的东西 —— 不是被清理，是根本不在那儿。只有 profile
（`C:\Users\hh25303`）跟着你走。所以 D: 上那份当"现在就用"，profile 那份当"明天还在"。

profile 卷只有 5.5 GB 空余，所以**拷到 U 盘/另一台机器之后，把 profile 里那份删掉**。

---

## 4. 为什么给的是便携包，不是安装器

项目里确实有一条安装器路径（`integrations/torcs-1.3.9/build-windows.ps1 -Action install`，
产出 `ApexStudySetup.exe`）。看了它的 NSIS 脚本，它一共做三件事：

1. 把载荷解到 `$LOCALAPPDATA\Apex`；
2. 建开始菜单和桌面快捷方式；
3. 写 HKCU 的卸载注册项。

没有 VC++ 运行库分发，没有服务，不需要管理员。**也就是说便携包和安装器铺下去的是同一份载荷**，
差的只是三个快捷方式和一个卸载项。

而重新编译安装器要 Visual Studio 2022 全量重编 TORCS，再加 NSIS —— 代价很大，换来三个快捷
方式。加上这边装东西本来就被策略挡（见 `windows-python-toolchain` 那条记忆），便携包在陌生
机器上反而更稳。

---

## 5. 另一台机器上的操作要点

完整版在包里的 `README-FIRST.txt`，这里只列最容易出事的几条：

- **解压到本地盘**（比如 `C:\Apex-Study`）。不要在 zip 预览窗口里直接跑，不要放网络盘或者
  还在同步的 OneDrive 目录 —— TORCS 要加载几百个小文件，在那些地方失败的样子很像"包坏了"。
- **必须在那台机器上本地登录，不能用远程桌面**。软件自己会拦：`graphical_session_issue()`
  看到 `SESSIONNAME` 以 `rdp` 开头就禁用开始按钮，并给出一句人话。这不是保守，是远程桌面的
  输入延迟会直接污染测量。
- Windows 会报「Windows protected your PC」（没签名）→ More info → Run anyway。被静默拦
  的话：右键 `Apex.exe` → 属性 → 勾 Unblock。
- 数据落在 `C:\Users\<那台机器的用户>\Apex\`，程序目录里什么都不写。想换地方就设
  `APEX_WORKSPACE`。
- 别手动搬 CSV。

---

## 6. 四个 phase 和分臂纪律

| 界面上显示 | 写进文件的值 |
|---|---|
| Baseline — first run, before any advice | `baseline` |
| Coached — second run, after AI advice | `coached` |
| Control — second run, own practice only, no AI advice | `control` |
| Familiarisation — not measured | `familiarisation` |

`baseline` 和 `control` **都是"没给辅导"**，区别只在于是第几次跑。所以：

> 分臂要在参与者坐下之前决定，phase 是照着分配填的，不是事后根据发生了什么补的。

这条不是形式主义。对照组存在的全部意义，就是让"第二次更快"这件事有两个可比的解释可以被分开：
被辅导了，还是同一条赛道又多跑了三圈。事后补标签等于把这个区分毁掉。

---

## 7. 数据怎么交回来

参与者跑完，在「Driving data saved」页面点 **Save a file to send**，默认存到桌面，得到一个
zip：里面是这次采集的全部文件 + 一份 `manifest`，逐文件记了字节数和 SHA-256。

把那些 zip 给我，我这边：

    racecoach collect <一堆 zip> --into <汇总目录>

它会逐文件核对校验和再解包 —— 路上掉了字节会被抓出来，而不是安静地进分析。

那个按钮只管交付，圈速在采集完成时就已经存好了。老的 session 也可以事后从 Garage 里再导。

---

## 8. AI 辅导要的 2.1 GB 权重不在包里

这是刻意的：安装器本身也不带权重，让程序首次使用时自己下载并校验。

- **baseline 和 control 完全不需要模型**。要是这趟只采这两臂，包里的东西已经齐了。
- 需要 `coached` 臂的话，缺权重时软件显示 **AI SETUP NEEDED**（意思是 llama.cpp 运行时是好
  的，只差权重），不是 AI UNAVAILABLE。

两条路：

1. **拷过去**。你本机已经有一份，我校验过了：
   `C:\Users\hh25303\Apex\models\granite-4.1-3b-Q4_K_M.gguf`，2,099,501,664 字节，和代码里
   钉的大小一致。拷到那台机器后：

       racecoach.exe install-model D:\somewhere\granite-4.1-3b-Q4_K_M.gguf

   它会先核对大小和 SHA-256 再收下，拷坏了会被拒绝而不是将就用。

2. **让软件自己下**。断点续传，校验同一个摘要。源是 huggingface.co，备源
   hf-mirror.com（有些网络到 HF 不通）。

2.1 GB 没塞进 zip：塞进去 zip 会变成 3 GB 多，而且 gguf 是量化过的，压缩基本无效 —— 单独拷
这个文件更快也更好断点。

---

## 9. 刻意没放进包里的东西

| 没放 | 为什么 |
|---|---|
| `uninstall.exe` | 它是 NSIS 的卸载器。在便携副本里跑它会去删并不存在的注册项、并试图删掉整个目录。便携版的卸载方式就是删文件夹。 |
| 六代 `Apex.exe.bak-*` / `racecoach.exe.bak-*` 和 `_internal.bak-20260823` | 约 340 MB 的历史版本，是本机的回滚保险，不是参与者机器要的东西。 |
| Granite 权重 | 见上一节。 |

---

## 10. 两件值得你在出发前确认的事

**一、渲染尺寸是 1280×720，而且不该去动它。**

> **2026-08-26 更正。** 打包时我看到 `torcs-runtime/config/screen.xml` 是 TORCS 原版默认的
> 640×480，就写成"现在是 640×480"。这是错的，别照着去改。那个文件只是**模板**：
> `_write_screen_config()`（`human_capture.py`）在每次启动前，把 preset 里的
> `window_width/height` 写进**这次 session 自己的 TORCS profile** 的 `screen.xml`，所以真正
> 跑起来的是 preset 说的 1280×720。另一台机器采回来的 capture manifest 里
> `"window_width": 1280, "window_height": 720`，实测确认。

尺寸冻在 preset 里（`default_study_preset`），并且逐场写进 manifest —— "不同的渲染尺寸就是
不同的条件"这一点软件自己在守。**要改就得改 preset，并且在收第一个参与者之前改**；改
`torcs-runtime/config/screen.xml` 既没用（启动前会被覆盖），也不会留下任何痕迹。

**二、输入设备要保持一致。** 键盘和手柄的圈速差可以轻松盖过辅导带来的差。换机器时把输入方式
也一起带过去，或者至少记下来。

---

## 11. 打包过程里踩到的一个坑（值得记下来）

第一版 zip 是用 PowerShell 5.1 的
`[System.IO.Compression.ZipFile]::CreateFromDirectory` 打的。打完检查条目名，发现里面全是
**反斜杠**：`Apex-Study\_internal\...`。

ZIP 规范（APPNOTE 4.4.17.1）要求条目名用正斜杠。.NET Framework 4.x 在 Windows 上写的是
路径分隔符原样，于是产出一个不合规的包 —— 换个解压工具就可能把整棵目录树摊成一堆文件名里
带反斜杠的怪文件。这种失败在陌生机器上看起来正好就像"包坏了"，而且是在参与者已经坐在那儿
的时候才发现。

改用 Windows 自带的 `tar.exe`（其实是 bsdtar / libarchive）重打：

    tar -a -c -f Apex-Study-2026-08-27.zip -C D:\apex-dist Apex-Study

条目名是正斜杠，单一根目录 `Apex-Study/`。

然后**真的解开验了一遍**，而不是只看条目名。2026-08-27 第二次这一版：解压出来
**3884 个文件、1,058,463,897 字节**，与源目录逐项相同；`Apex.exe` / `racecoach.exe` /
`wtorcs.exe` / `base_library.zip` 四个逐个 SHA-256 一致；从**解压出来的那份**跑
`racecoach.exe --help`、`study-exposure --help`、`study-adherence --help` 全部退出码 0；
并且**启动了解压出来的 `Apex.exe`**，4.8 秒拿到窗口标题 "Apex"，干净退出，退出码 0。

> 第一次（09:55）跳过了启动检查 —— 当时研究者自己的 Apex 正开着、llama-server 也在跑，
> 在活动会话上再起一个实例不值得。11:00 这次他关掉之后补上了，装好的那份
> `C:\Users\hh25303\repos\Apex\Apex.exe` 也单独跑了一次（3.7 秒，标题 "Apex"，退出码 0）。

---

## 12. 2026-08-28 的这一版

换掉的是 Session Debrief 的一个 bug：在一份 debrief 还没算完的时候再点一次
"Session debrief"，会把在跑的那个丢掉、再排一个新的进去 —— 每点一次多一份，而且
排的是 Lap Analysis 共用的那个**单线程**池。原因、测试为什么没抓到、以及一处**没有**
在这一版里修的同类问题，都写在 `docs/STUDY_CAN_WE_SHOW_IMPROVEMENT.md` §12。

    Apex.exe        9942BD69C546E76C1BDB1D1CD93102D82924B14CAF11857520D471A8B8D118C5
    racecoach.exe   1EEA2499E25B5238FA89DBA7BC0D57EE729736094E65C898463E895FD1B733C6
    Apex-Study-2026-08-28.zip
                    E6E4A1CFBF0EAB67990358889EF57CEFE6EED822CFBE34DF6B79E673F386DC9E
                    604,743,428 字节

源码状态是 commit `f301231` **加上两个文件**（`src/apex/debrief_view.py`、
`tests/test_debrief_view.py`），除此之外工作区是干净的 —— 比 08-27 那三次
（9 个未提交文件）好查得多。

被换下来的存成 `Apex.exe.bak-20260827-1203` / `racecoach.exe.bak-20260827-1203`。
注意换的时候研究者的 Apex 正开着（PID 6612），所以是**改名**而不是覆盖 —— 已经映射
进进程的镜像可以改名，不能覆盖；那个进程仍然跑在改名后的那份上，要**重启**才会用到
新的。

这一版是"读出来确认"而不是"按时间戳相信"的：把新 `Apex.exe` 里的 zlib 流全部解开
（1688 段），找到两段这次新增的 docstring；同样的检查跑在被替换掉的 exe 上，两段都
不在。装好的那份 `Apex.exe` 也真的启动过一次，拿到窗口标题 "Apex"，干净退出。

**这一版里没有什么，也要写清楚。** 打完包之后（08-28 中午）工作区里又多了一批改动，
其中 `racecoach/granite/report.py` 改了**参与者读到的内容**：最快的那一圈原本只拿到
一句"This was your quickest lap of the session."和零条发现，现在改成拿它自己每个弯
被开得最好的那次当参照，于是也会有发现、也会被叙述。**那批改动不在上面这两个哈希里。**
要不要把它发给参与者、以及能不能在已经采了三个包之后中途换，是研究设计上的决定，
不是打包决定 —— 见 `docs/STUDY_CAN_WE_SHOW_IMPROVEMENT.md` §12.8。

> 08-27 那两个 zip（`A9C5FA5C…`、`BD12E197…`、`03943F7A…`）都作废了。文件名带日期，
> 但认哈希更稳，或者解开看 `apex-study-build.txt` 第一行写的是哪天。旧的 zip 没有删。

---

## 13. 交叉引用

- 采集协议全文：包里的 `docs\HUMAN_TELEMETRY_CAPTURE.md`（也在仓库 `docs/` 下）
- 通道清单：`docs\DATA_AVAILABILITY.md`
- 这批数据回来之后能论证什么、不能论证什么：`docs/STUDY_CAN_WE_SHOW_IMPROVEMENT.md`（数据交回来之后的处理、以及那条路上修掉的几个坑，在它的 §10）
- 构建来源与校验和：包里的 `apex-study-build.txt`
