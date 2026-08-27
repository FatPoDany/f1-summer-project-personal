# 便携研究版打包记录（2026-08-26）

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
| `D:\apex-dist\Apex-Study\` | 解开的目录，可以直接拷 U 盘 | 1002 MB / 3882 个文件 |
| `D:\apex-dist\Apex-Study-2026-08-26.zip` | 打好的 zip | **574.1 MB** |
| `C:\Users\hh25303\Apex-Study-2026-08-26.zip` | **同一个 zip 的副本，放在 profile 里** | 574.1 MB |
| 两处各有一个 `.zip.sha256` | 拷贝之后核对用 |  |

    SHA-256  835B684B3EC88223A3045C7BD2110F7789B3D06BCF02A5F035A4D4B56B9D6FAC

到了另一台机器上核对：

    certutil -hashfile Apex-Study-2026-08-26.zip SHA256

解压后是一个 `Apex-Study\` 目录（不会把 3882 个文件散到当前目录里）。

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

    tar -a -c -f Apex-Study-2026-08-26.zip -C D:\apex-dist Apex-Study

条目名是正斜杠，单一根目录 `Apex-Study/`，4230 个条目。

然后**真的解开验了一遍**，而不是只看条目名：解压出来 3882 个文件、总字节数与源目录完全相同，
`Apex.exe` / `racecoach.exe` / `wtorcs.exe` / `base_library.zip` 逐个 SHA-256 一致，并且从
**解压出来的那份**跑 `racecoach.exe --help` 退出码 0、启动 `Apex.exe` 拿到窗口标题 "Apex"
后干净退出。

---

## 12. 交叉引用

- 采集协议全文：包里的 `docs\HUMAN_TELEMETRY_CAPTURE.md`（也在仓库 `docs/` 下）
- 通道清单：`docs\DATA_AVAILABILITY.md`
- 这批数据回来之后能论证什么、不能论证什么：`docs/STUDY_CAN_WE_SHOW_IMPROVEMENT.md`（数据交回来之后的处理、以及那条路上修掉的几个坑，在它的 §10）
- 构建来源与校验和：包里的 `apex-study-build.txt`
