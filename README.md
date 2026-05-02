# Algorithm Contest Scheduler Agent

一个本地桌面版算法竞赛日程 Agent。双击 `run_agent.bat` 后会打开聊天式界面，可以刷新未来 7 天赛程、打开比赛链接、导出 Markdown。

当前 UI 使用 `assets/background.png` 作为窗口背景图。
标题栏图标使用 `assets/title_icon.ico`，任务栏图标使用 `assets/taskbar_icon.ico`。

## 使用方式

1. 双击 `run_agent.bat`。
2. 在输入框里输入 `刷新赛程`，或点击右上角 `刷新赛程`。
3. 左侧选择比赛后点击 `打开选中链接`。
4. 点击 `导出 Markdown` 会生成 `latest_contests.md`。
5. 默认开启赛前 30 分钟提醒。程序运行时会每分钟检查一次，命中后弹窗提醒。

## 当前数据源

- Codeforces 官方 API：重点过滤 Div.2 / Div.3 / Div.4 / Educational / Div.1 + Div.2。
- LeetCode 官方 GraphQL：周赛与双周赛。
- AtCoder 官方 contests 页面：ABC 与 ARC。
- 牛客公开比赛列表：牛客系列赛、高校同步赛等公开 ACM/OI 比赛。
- 洛谷：通过 CLIST 聚合页读取洛谷公开比赛条目。

蓝桥云课暂未接入稳定公开接口。本程序会直接省略未可靠确认的赛事，避免生成错误时间和链接。

## 指令

- `刷新赛程`
- `打开选中链接`
- `导出 Markdown`
- `开启提醒`
- `关闭提醒`
- `帮助`

## 环境

需要本机已安装 Python 3，程序只使用标准库，不需要安装第三方依赖。
