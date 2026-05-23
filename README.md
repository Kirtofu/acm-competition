# Algorithm Contest Scheduler Agent

本地桌面版算法竞赛日程 Agent。双击 `run_agent.bat` 后打开聊天式界面，自动聚合近期算法竞赛，支持赛前提醒、平台筛选、Rating 跟踪、每日推荐题、Markdown 导出和 iCalendar 日历导出。

当前 UI 使用 `assets/background.png` 作为窗口背景图；标题栏图标使用 `assets/title_icon.ico`，任务栏图标使用 `assets/taskbar_icon.ico`。

## 功能

- 未来 7 天赛程聚合：统一转换为北京时间，展示比赛名称、平台、开始时间、时长、报名/来源备注和直达链接。
- 进行中与补题区：列表底部显示 14 天内已结束赛事，方便赛后补题。
- 平台筛选：左侧 checkbox 可控制 `CF`、`ATC`、`LeetCode`、`牛客`、`洛谷`、`蓝桥`、`XCPC` 的显示。
- 冲突提示：同时段比赛会用 `⚠` 标记。
- 赛前提醒：默认赛前 30 分钟提醒，Windows Toast 会尽力发送，同时始终弹出应用内置顶提醒窗，避免系统通知被静默吞掉。
- 提醒测试：点击 `测提醒` 或输入 `测试提醒`，可以立刻验证提醒链路。
- Rating 跟踪：支持 Codeforces / AtCoder handle，显示当前分、历史最高和最近 5 场变化。
- 每日推荐：基于 Codeforces rating 推荐 3 道题，并过滤已 AC 题目。
- 导出：支持 Markdown 速递表和 RFC 5545 iCalendar 文件。

## 使用方式

1. 确认本机已安装 Python 3.10+。
2. 双击 `run_agent.bat`。
3. 点击 `刷新赛程`，或在输入框输入 `刷新`。
4. 选中左侧比赛后点击 `打开选中链接`，也可以双击或按 Enter 打开。
5. 点击 `Rating` 配置 CF / AtCoder handle。
6. 点击 `推荐` 获取 Codeforces 每日推荐题。
7. 点击 `Markdown` 或 `ICS` 导出赛程文件。
8. 点击 `测提醒` 验证系统通知和应用内提醒窗是否正常。

## 数据源

- Codeforces 官方 API：Div.2 / Div.3 / Div.4 / Educational / Div.1 + Div.2，外加 rating / problemset / submissions。
- LeetCode 官方 GraphQL：周赛与双周赛。
- AtCoder 官方 contests 页面：ABC / ARC，外加 user rating history。
- 牛客公开比赛列表：牛客系列赛、高校同步赛等公开 ACM/OI 比赛。
- 洛谷官方 contest list：解析页面中的 `lentille-context` 数据。
- 蓝桥云课公开 API：仅保留算法相关科目，包括蓝桥杯 / C++ / C / Java / Python / 不限语言。

ICPC / CCPC / XCPC 区域赛暂无统一公开接口。程序会扫描所有来源中带 `ICPC`、`CCPC`、`XCPC`、`区域赛`、`省赛`、`国赛`、`重现赛`、`同步赛`、`Regional` 等关键字的比赛，并重标为 `XCPC` 方便统一筛选。

## 指令

- `刷新`：拉取最新赛程。
- `打开`：打开左侧选中的比赛链接。
- `导出` / `markdown`：导出 `latest_contests.md`。
- `日历` / `ics` / `calendar`：导出 `latest_contests.ics`。
- `rating` / `分数` / `积分`：打开 Rating 面板。
- `推荐` / `题目` / `刷题`：获取每日推荐题。
- `handle`：配置 CF / AtCoder 用户名。
- `提醒`：开启赛前 30 分钟提醒。
- `关闭提醒`：关闭赛前提醒。
- `测试提醒`：立即弹出一条测试通知。
- `帮助`：查看内置说明。

## 输出文件

- `latest_contests.md`：Markdown 赛程速递表。
- `latest_contests.ics`：iCalendar 文件，内置 30 分钟 `VALARM`，可导入 Outlook / Google Calendar / Apple Calendar / 手机系统日历。
- `contest_cache.json`：本地缓存和偏好设置，包括自动刷新、提醒开关、平台筛选状态和 handles。

这些文件是运行时产物，默认不会提交到仓库。

## 项目结构

- `contest_agent.pyw`：Agent 主程序。
- `run_agent.bat`：Windows 双击启动脚本。
- `assets/`：背景图和窗口图标资源。
- `README.md`：项目说明。

## 环境

程序只使用 Python 标准库，不需要安装第三方依赖。Windows 10/11 上 Toast 通知通过 PowerShell 调用 WinRT；如果系统没有展示 Toast，Agent 仍会弹出自己的置顶提醒窗。
