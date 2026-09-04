# Apex 与参与者网站架构方案

> **English summary — *Apex and the participant website: where the boundary
> goes.*** A working note in the language it was thought in, proposing how the
> desktop application and the team's participant-facing website should divide the
> work.
>
> The position argued is that **Apex is the whole application and the website is a
> light entry point for participants**, with both sides sharing one set of data,
> analysis and feedback rather than each implementing its own. Apex should be a
> superset of the website, so a participant who opens Apex can complete the entire
> procedure without it. The document carries a capability table showing which side
> owns what, the recommended architecture, two modes Apex should grow (a
> participant mode and a researcher mode), an audit of what Apex already had
> against what it lacked, a suggested build order, and the architectural decisions
> still to be confirmed with the team.

最终架构应当是：

> Apex 是完整的主应用；网站是面向参与者的轻量入口。  
> 两者共享同一套数据、分析和反馈能力，不能分别实现两套算法。

Apex 应当是网站功能的超集，而不是后台工具。参与者即使进入 Apex，也能完成网站上的完整流程。

## 两端功能边界

| 能力 | 参与者网站 | Apex |
|---|---:|---:|
| 实验说明与同意书 | ✓ | ✓ |
| 驾驶经验问卷 | ✓ | ✓ |
| 键盘/手柄检测与校准 | ✓ | ✓ |
| 获取实验编号和实验阶段 | ✓ | ✓ |
| 启动规定的 TORCS 场景 | 通过 Apex | ✓ |
| 遥测采集、校验和本地保存 | — | ✓ |
| 自动上传及失败重试 | 显示状态 | ✓ |
| 圈速和驾驶指标 | ✓ | ✓ |
| Granite 个性化建议 | ✓ | ✓ |
| 遥测曲线及证据定位 | 简化版 | 完整版 |
| 多圈、多人、基线对比 | 个人结果 | 完整研究视图 |
| 离线导入、导出报告 | — | ✓ |
| 实验配置和数据质量检查 | — | ✓ |

这里的“网站没有某个高级研究功能”不影响 Apex 实现它；反过来，网站已有的参与者功能必须在 Apex 中也能完成。

## 推荐架构

```text
                         ┌─ 参与者网站
                         │  问卷、校准、实验入口、个人反馈
共享实验服务与数据契约 ──┤
                         │
                         └─ Apex 桌面端
                            问卷、校准、启动 TORCS、采集、
                            分析、Granite、对比、研究管理
                                      │
                                      ▼
                           本地 TORCS + 遥测记录器
```

关键原则是“共享能力，不共享界面”：

- `f1coach_core` 继续作为遥测分析、证据生成和反馈验证的唯一事实来源。
- 网站和 Apex 可以有不同的页面设计，但显示同一份分析结果。
- 一次驾驶产生一个统一的 `Session Bundle`，包含：
  - 匿名参与者编号；
  - 实验组、阶段、赛道和车辆；
  - 问卷与设备信息；
  - 原始遥测 CSV；
  - 数据质量结果；
  - 圈速与事件指标；
  - Granite 反馈及模型、提示词和算法版本；
  - 文件校验值与时间戳。
- 上传必须支持断线重试，网络失败不能造成遥测丢失或阻止本地分析。
- Apex 应支持离线使用；恢复网络后再同步。

## Apex 建议增加两种模式

### 参与者模式

提供逐步向导：同意书 → 问卷 → 设备校准 → 练习 → 基线/辅导驾驶 → 上传 → 反馈。隐藏复杂配置，参与者不需要看到路径、终端或 Granite 参数。

### 研究者模式

保留并扩展现有 Garage、Collect Data、Lap Analysis、Compare 和 Granite 功能，增加实验配置、数据完整性、参与者会话和批量导出。

需要注意：功能虽然存在于 Apex，但正式对照实验中，未接受辅导组在主要终点完成前必须被实验权限隐藏反馈，不能让参与者自行打开 Lap Analysis 绕过分组。

## 当前 Apex 已有与尚缺功能

现有基础已经不少：

- 图形化 Collect Data；
- 自动启动和停止 TORCS；
- 真人遥测采集与 CSV 校验；
- Garage、单圈分析和多圈比较；
- Granite 证据约束反馈；
- 本地结果和报告。

下一阶段真正缺少的是：

1. 同意书和前置问卷；
2. 输入设备检测、校准与练习；
3. 固定赛道、车辆和实验条件的一键启动；
4. 服务端分配的参与者编号、实验组和阶段；
5. 标准化 Session Bundle；
6. 自动上传、断线队列和重复上传保护；
7. 网站与 Apex 共用的实验及反馈 API；
8. Apex 参与者模式；
9. 安装包和网站一键唤起 Apex；
10. 服务端个人反馈页面。

## 建议开发顺序

```text
统一 Session Bundle 数据契约
    ↓
Apex 本地完整实验闭环
    ↓
设备校准和固定场景启动
    ↓
上传/同步服务
    ↓
参与者网站
    ↓
网页唤起 Apex
    ↓
小规模内部试运行
    ↓
正式对比用户研究
```

不要先开发完整网站。第一条可验收的纵向流程应该是：

> 在 Apex 填问卷 → 校准 → 一键启动 TORCS → 完成规定圈数 → 自动采集 → 本地分析 → Granite 反馈 → 生成可上传的 Session Bundle。

这条跑通后，网站只是为同一流程增加远程入口，不会反过来拖慢核心遥测功能。

## 待确认的架构决策

Apex 作为功能完整的主应用，并增加“参与者模式”和“研究者模式”；网站只服务参与者，但复用同一数据和反馈契约。确认后再把它写成正式规格和分阶段任务，随后逐项实施。
