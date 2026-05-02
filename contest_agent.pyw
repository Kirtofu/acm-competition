import ctypes
import json
import random
import re
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, TOP, BOTTOM, X, Y, BooleanVar, Button, Canvas, Checkbutton, Entry, Frame, Label, Listbox, PhotoImage, Scrollbar, Text, Tk, messagebox


APP_DIR = Path(__file__).resolve().parent
CACHE_FILE = APP_DIR / "contest_cache.json"
LATEST_MD_FILE = APP_DIR / "latest_contests.md"
BACKGROUND_IMAGE_FILE = APP_DIR / "assets" / "background.png"
TITLE_ICON_FILE = APP_DIR / "assets" / "title_icon.ico"
TASKBAR_ICON_FILE = APP_DIR / "assets" / "taskbar_icon.ico"
BEIJING = timezone(timedelta(hours=8), "UTC+8")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AlgorithmContestAgent/1.0"


@dataclass
class Contest:
    platform: str
    title: str
    start: datetime
    duration_seconds: int
    link: str
    note: str = "报名状态以平台页面为准"

    def duration_label(self) -> str:
        minutes = max(1, self.duration_seconds // 60)
        hours, mins = divmod(minutes, 60)
        if hours and mins:
            return f"{hours}小时{mins}分"
        if hours:
            return f"{hours}小时"
        return f"{mins}分钟"

    def compact_time(self) -> str:
        return self.start.strftime("%m月%d日 %H:%M")

    def to_json(self) -> dict:
        return {
            "platform": self.platform,
            "title": self.title,
            "start": self.start.isoformat(),
            "duration_seconds": self.duration_seconds,
            "link": self.link,
            "note": self.note,
        }

    @staticmethod
    def from_json(raw: dict) -> "Contest":
        return Contest(
            platform=raw["platform"],
            title=raw["title"],
            start=datetime.fromisoformat(raw["start"]),
            duration_seconds=int(raw["duration_seconds"]),
            link=raw["link"],
            note=raw.get("note", "报名状态以平台页面为准"),
        )


def request_json(url: str, data: bytes | None = None, headers: dict | None = None) -> dict:
    merged_headers = {"User-Agent": USER_AGENT, "Accept": "application/json,text/plain,*/*"}
    if headers:
        merged_headers.update(headers)
    req = urllib.request.Request(url, data=data, headers=merged_headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def request_text(url: str) -> str:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return resp.read().decode("utf-8", "ignore")


def in_window(start: datetime, now: datetime, days: int) -> bool:
    return now <= start <= now + timedelta(days=days)


def ms_to_beijing(timestamp_ms: int) -> datetime:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).astimezone(BEIJING)


def fetch_codeforces(now: datetime, days: int) -> list[Contest]:
    data = request_json("https://codeforces.com/api/contest.list?gym=false")
    contests: list[Contest] = []
    focus = re.compile(r"(Div\. ?[234]|Educational|Div\. ?1 \+ Div\. ?2)", re.I)
    for item in data.get("result", []):
        if item.get("phase") != "BEFORE":
            continue
        name = item.get("name", "")
        if not focus.search(name):
            continue
        start_ts = item.get("startTimeSeconds")
        if not start_ts:
            continue
        start = datetime.fromtimestamp(int(start_ts), timezone.utc).astimezone(BEIJING)
        if in_window(start, now, days):
            contests.append(
                Contest(
                    platform="CF",
                    title=name,
                    start=start,
                    duration_seconds=int(item.get("durationSeconds", 0)),
                    link=f"https://codeforces.com/contest/{item.get('id')}",
                    note="适合练手速；赛前确认是否 rated",
                )
            )
    return contests


def fetch_leetcode(now: datetime, days: int) -> list[Contest]:
    query = {
        "query": "query getContestUpcoming { allContests { title titleSlug startTime duration } }",
    }
    body = json.dumps(query).encode("utf-8")
    data = request_json(
        "https://leetcode.com/graphql",
        data=body,
        headers={
            "Content-Type": "application/json",
            "Referer": "https://leetcode.com/contest/",
            "Origin": "https://leetcode.com",
        },
    )
    contests: list[Contest] = []
    for item in data.get("data", {}).get("allContests", []):
        title = item.get("title", "")
        if not (title.startswith("Weekly Contest") or title.startswith("Biweekly Contest")):
            continue
        start = datetime.fromtimestamp(int(item["startTime"]), timezone.utc).astimezone(BEIJING)
        if in_window(start, now, days):
            contests.append(
                Contest(
                    platform="LeetCode",
                    title=title,
                    start=start,
                    duration_seconds=int(item.get("duration", 5400)),
                    link=f"https://leetcode.com/contest/{item.get('titleSlug')}/",
                    note="周赛/双周赛，注意提前登录",
                )
            )
    return contests


def fetch_nowcoder(now: datetime, days: int) -> list[Contest]:
    contests: list[Contest] = []
    seen: set[str] = set()
    urls = [
        "https://ac.nowcoder.com/acm/contest/vip-index?topCategoryFilter=13&categoryFilter=-1&onlyCreateFilter=false&orderType=NO&rankTypeFilter=0",
        "https://ac.nowcoder.com/acm/contest/vip-index?topCategoryFilter=14&categoryFilter=-1&onlyCreateFilter=false&orderType=NO&rankTypeFilter=0",
    ]
    for url in urls:
        html = request_text(url)
        for raw in re.findall(r'data-json="(.*?)"', html, flags=re.S | re.I):
            try:
                data = json.loads(unescape(unescape(raw)))
            except json.JSONDecodeError:
                continue
            contest_id = str(data.get("contestId") or "")
            if not contest_id or contest_id in seen:
                continue
            seen.add(contest_id)
            start_ms = data.get("contestStartTime")
            duration_ms = data.get("contestDuration", 0)
            if not start_ms:
                continue
            start = ms_to_beijing(int(start_ms))
            if not in_window(start, now, days):
                continue
            title = unescape(str(data.get("contestName", "")).strip())
            if not title:
                continue
            sign_up_end = data.get("contestSignUpEndTime")
            note = "报名状态以牛客页面为准"
            if sign_up_end:
                sign_up_end_time = ms_to_beijing(int(sign_up_end))
                if sign_up_end_time >= now:
                    note = f"报名中；报名截止 {sign_up_end_time.strftime('%m月%d日 %H:%M')}"
            contests.append(
                Contest(
                    platform="牛客",
                    title=title,
                    start=start,
                    duration_seconds=int(duration_ms) // 1000,
                    link=f"https://ac.nowcoder.com/acm/contest/{contest_id}",
                    note=note,
                )
            )
    return contests


def parse_clist_time(raw: str) -> datetime:
    return datetime.strptime(raw, "%B %d, %Y %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(BEIJING)


def fetch_luogu(now: datetime, days: int) -> list[Contest]:
    html = request_text("https://clist.by/")
    rows = re.findall(r"<tr class=\"contest.*?</tr>", html, flags=re.S | re.I)
    contests: list[Contest] = []
    seen: set[str] = set()
    for row in rows:
        if "luogu.com.cn" not in row:
            continue
        data_match = re.search(r"data-ace='(.*?)'", row, flags=re.S | re.I)
        if not data_match:
            continue
        try:
            data = json.loads(unescape(data_match.group(1)))
        except json.JSONDecodeError:
            continue
        time_info = data.get("time", {})
        start_raw = time_info.get("start")
        end_raw = time_info.get("end")
        if not start_raw:
            continue
        start = parse_clist_time(start_raw)
        end = parse_clist_time(end_raw) if end_raw else start
        if not (in_window(start, now, days) or start <= now <= end):
            continue
        link = str(data.get("desc", "")).replace("url:", "").strip()
        if not link:
            title_link = re.search(r'<a class="title-search" href="([^"]+)"', row, flags=re.S | re.I)
            link = unescape(title_link.group(1)) if title_link else "https://www.luogu.com.cn/contest/list"
        key = link or str(data.get("title", ""))
        if key in seen:
            continue
        seen.add(key)
        title = unescape(str(data.get("title", "")).strip())
        duration_seconds = max(0, int((end - start).total_seconds()))
        note = "来自 CLIST 聚合；报名以洛谷页面为准"
        if start <= now <= end:
            note = "正在进行；来自 CLIST 聚合，报名以洛谷页面为准"
        contests.append(
            Contest(
                platform="洛谷",
                title=title,
                start=start,
                duration_seconds=duration_seconds,
                link=link,
                note=note,
            )
        )
    return contests


def parse_atcoder_duration(raw: str) -> int:
    parts = raw.strip().split(":")
    if len(parts) != 2:
        return 0
    return int(parts[0]) * 3600 + int(parts[1]) * 60


def strip_tags(raw: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", raw)).strip()


def fetch_atcoder(now: datetime, days: int) -> list[Contest]:
    html = request_text("https://atcoder.jp/contests/")
    section = html.split("Upcoming Contests</h3>", 1)
    if len(section) < 2:
        return []
    upcoming = section[1].split("</tbody>", 1)[0]
    rows = re.findall(r"<tr>(.*?)</tr>", upcoming, flags=re.S | re.I)
    contests: list[Contest] = []
    for row in rows:
        time_match = re.search(r"<time[^>]*>(.*?)</time>", row, flags=re.S | re.I)
        link_match = re.search(r'<a href="(/contests/[^"]+)">(.*?)</a>', row, flags=re.S | re.I)
        if not link_match:
            link_match = re.search(r"<a href='(/contests/[^']+)'>(.*?)</a>", row, flags=re.S | re.I)
        duration_match = re.search(r'<td class="text-center">(\d{2}:\d{2})</td>', row, flags=re.S | re.I)
        if not (time_match and link_match and duration_match):
            continue
        title = unescape(strip_tags(link_match.group(2)))
        if "Beginner Contest" not in title and "Regular Contest" not in title:
            continue
        start_raw = strip_tags(time_match.group(1))
        start = datetime.strptime(start_raw, "%Y-%m-%d %H:%M:%S%z").astimezone(BEIJING)
        if in_window(start, now, days):
            contests.append(
                Contest(
                    platform="ATC",
                    title=title,
                    start=start,
                    duration_seconds=parse_atcoder_duration(duration_match.group(1)),
                    link=f"https://atcoder.jp{link_match.group(1)}",
                    note="ABC/ARC，开赛前确认 rated range",
                )
            )
    return contests


def fetch_all_contests(days: int = 7) -> tuple[list[Contest], list[str]]:
    now = datetime.now(BEIJING)
    all_contests: list[Contest] = []
    errors: list[str] = []
    sources = [
        ("Codeforces", fetch_codeforces),
        ("LeetCode", fetch_leetcode),
        ("AtCoder", fetch_atcoder),
        ("牛客", fetch_nowcoder),
        ("洛谷/CLIST", fetch_luogu),
    ]
    for name, func in sources:
        try:
            all_contests.extend(func(now, days))
        except Exception as exc:
            errors.append(f"{name}: {exc}")
    all_contests.sort(key=lambda item: item.start)
    save_cache(all_contests)
    return all_contests, errors


def save_cache(contests: list[Contest]) -> None:
    payload = {
        "updated_at": datetime.now(BEIJING).isoformat(),
        "contests": [item.to_json() for item in contests],
    }
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cache() -> tuple[list[Contest], str | None]:
    if not CACHE_FILE.exists():
        return [], None
    try:
        payload = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        contests = [Contest.from_json(item) for item in payload.get("contests", [])]
        return contests, payload.get("updated_at")
    except Exception:
        return [], None


def cpp_tip(contests: list[Contest]) -> str:
    high_weight = any(
        ("Div. 2" in c.title or "Educational" in c.title or "蓝桥" in c.title)
        for c in contests
    )
    tips = [
        "不要忘记复习 Dijkstra 的优先队列优化写法，时间复杂度 O(M log N)。",
        "线段树模板建议默写 build / pushup / query / modify，比赛里别临场调下标。",
        "背包 DP 先判断 0/1、完全、多重三类转移方向，滚动数组别写反。",
        "BFS 迷宫题先统一 dx/dy、vis 入队即标记，避免重复入队炸复杂度。",
        "并查集路径压缩和按秩/大小合并要成套写，连通性题能省很多时间。",
    ]
    if high_weight:
        return random.choice(tips)
    return "今天先把二分答案的 check 单调性想清楚，别急着上代码。"


def render_markdown(contests: list[Contest], errors: list[str] | None = None) -> str:
    now = datetime.now(BEIJING)
    urgent = [c for c in contests if c.start <= now + timedelta(hours=24) or c.note.startswith("正在进行")]
    later = [c for c in contests if c.start > now + timedelta(hours=24)]
    lines: list[str] = []
    lines.append("🏆 **近期算法竞赛速递表**")
    lines.append("")
    lines.append(f"> 更新时间：{now.strftime('%m月%d日 %H:%M')}，北京时间 UTC+8。")
    lines.append("")
    lines.append("🔥 **[24小时内 / 进行中] 紧急备战**")
    if urgent:
        for c in urgent:
            lines.append(f"- **[{c.platform}]** {c.title}")
            lines.append(f"  - ⏰ 时间: {c.compact_time()} (时长: {c.duration_label()})")
            lines.append(f"  - 🔗 链接: [点击直达]({c.link})")
            lines.append(f"  - 💡 备注: {c.note}")
    else:
        lines.append("- 暂无已确认的目标赛事。")
    lines.append("")
    lines.append("📅 **[未来 3-7 天] 赛事预告**")
    if later:
        for c in later:
            lines.append(f"- **[{c.platform}]** {c.title} | {c.compact_time()} | [链接]({c.link})")
    else:
        lines.append("- 暂无已确认的目标赛事。")
    lines.append("")
    lines.append("🛠️ **今日 C++ 锦囊**")
    lines.append(f"> {cpp_tip(contests)}")
    if errors:
        lines.append("")
        lines.append("⚠️ **数据源状态**")
        for err in errors:
            lines.append(f"- {err}")
    lines.append("")
    lines.append("数据源：Codeforces 官方 API、LeetCode 官方 GraphQL、AtCoder 官方赛程、牛客公开列表、CLIST 洛谷聚合。蓝桥云课未配置可靠爬虫时不输出。")
    return "\n".join(lines)


def render_ui_report(contests: list[Contest], errors: list[str] | None = None) -> str:
    now = datetime.now(BEIJING)
    urgent = [c for c in contests if c.start <= now + timedelta(hours=24) or c.note.startswith("正在进行")]
    later = [c for c in contests if c.start > now + timedelta(hours=24)]
    lines: list[str] = []
    lines.append("近期算法竞赛速递表")
    lines.append(f"更新时间：{now.strftime('%m月%d日 %H:%M')}  北京时间 UTC+8")
    lines.append("")
    lines.append("[24小时内 / 进行中] 紧急备战")
    if urgent:
        for idx, contest in enumerate(urgent, 1):
            lines.extend(format_ui_contest(idx, contest, detailed=True))
    else:
        lines.append("暂无已确认的目标赛事。")
    lines.append("")
    lines.append("[未来 3-7 天] 赛事预告")
    if later:
        for idx, contest in enumerate(later, 1):
            lines.extend(format_ui_contest(idx, contest, detailed=False))
    else:
        lines.append("暂无已确认的目标赛事。")
    lines.append("")
    lines.append("今日 C++ 锦囊")
    lines.append(cpp_tip(contests))
    if errors:
        lines.append("")
        lines.append("数据源状态")
        for err in errors:
            lines.append(f"- {err}")
    lines.append("")
    lines.append("数据源：Codeforces 官方 API、LeetCode 官方 GraphQL、AtCoder 官方赛程、牛客公开列表、CLIST 洛谷聚合。")
    lines.append("洛谷官方页对脚本请求不稳定，当前以 CLIST 聚合数据为准；蓝桥云课未配置可靠爬虫时不输出。")
    return "\n".join(lines)


def format_ui_contest(idx: int, contest: Contest, detailed: bool) -> list[str]:
    lines = [
        f"{idx}. [{contest.platform}] {contest.title}",
        f"   时间：{contest.compact_time()}    时长：{contest.duration_label()}",
    ]
    if detailed:
        lines.append(f"   备注：{contest.note}")
    lines.append(f"   链接：{contest.link}")
    return lines


def configure_windows_app_id() -> None:
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("local.contest.scheduler.agent")
    except Exception:
        pass


class ContestAgentApp:
    def __init__(self) -> None:
        configure_windows_app_id()
        self.root = Tk()
        self.root.title("Algorithm Contest Scheduler Agent")
        self.root.geometry("1120x760")
        self.root.minsize(960, 620)
        self.root.configure(bg="#f6dce5")

        self.contests: list[Contest] = []
        self.auto_refresh = BooleanVar(value=False)
        self.reminder_enabled = BooleanVar(value=True)
        self.refreshing = False
        self.reminder_minutes = 30
        self.reminded_contests: set[str] = set()
        self.background_photo: PhotoImage | None = None
        self.background_item: int | None = None
        self.header_window: int | None = None
        self.left_window: int | None = None
        self.right_window: int | None = None
        self.icon_handles: list[int] = []

        self.build_ui()
        self.apply_window_icons()
        cached, updated_at = load_cache()
        if cached:
            self.contests = cached
            self.write_agent(f"已加载缓存赛程（{updated_at or '未知时间'}）。输入“刷新”获取最新数据。")
            self.render_contest_list()
            self.write_agent(render_ui_report(self.contests))
        else:
            self.write_agent("我是算法竞赛日程管家。输入“刷新赛程”或点击右上角按钮，我会拉取未来 7 天赛事。")
        self.schedule_auto_refresh()
        self.schedule_reminders()

    def build_ui(self) -> None:
        self.canvas = Canvas(self.root, bg="#f6dce5", highlightthickness=0, borderwidth=0)
        self.canvas.pack(fill=BOTH, expand=True)
        if BACKGROUND_IMAGE_FILE.exists():
            self.background_photo = PhotoImage(file=str(BACKGROUND_IMAGE_FILE))
            self.background_item = self.canvas.create_image(0, 0, image=self.background_photo, anchor="nw")

        def make_button(parent: Frame, text: str, command) -> Button:
            return Button(
                parent,
                text=text,
                command=command,
                bg="#f5c6d6",
                fg="#29242b",
                activebackground="#f0aeca",
                activeforeground="#1f1a21",
                relief="flat",
                borderwidth=0,
                padx=12,
                pady=7,
                font=("Microsoft YaHei UI", 9, "bold"),
                cursor="hand2",
            )

        header = Frame(self.canvas, bg="#fff3f7", padx=16, pady=10, highlightthickness=1, highlightbackground="#f3b8ca")
        Label(
            header,
            text="Contest Scheduler Agent",
            fg="#2b2730",
            bg="#fff3f7",
            font=("Consolas", 15, "bold"),
        ).pack(side=LEFT)
        Checkbutton(
            header,
            text="赛前30分钟提醒",
            variable=self.reminder_enabled,
            fg="#524955",
            bg="#fff3f7",
            activebackground="#fff3f7",
            activeforeground="#2b2730",
            selectcolor="#fff3f7",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=RIGHT, padx=(8, 0))
        Checkbutton(
            header,
            text="每小时自动刷新",
            variable=self.auto_refresh,
            fg="#524955",
            bg="#fff3f7",
            activebackground="#fff3f7",
            activeforeground="#2b2730",
            selectcolor="#fff3f7",
            font=("Microsoft YaHei UI", 9),
        ).pack(side=RIGHT, padx=(8, 0))
        make_button(header, "刷新赛程", self.refresh_async).pack(side=RIGHT, padx=5)
        make_button(header, "打开选中链接", self.open_selected).pack(side=RIGHT, padx=5)
        make_button(header, "导出 Markdown", self.export_markdown).pack(side=RIGHT, padx=5)

        left = Frame(self.canvas, bg="#fff8fb", padx=10, pady=10, highlightthickness=1, highlightbackground="#f3c8d5")
        Label(left, text="未来 7 天", fg="#6c5664", bg="#fff8fb", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        self.listbox = Listbox(
            left,
            width=42,
            bg="#fff8fb",
            fg="#2e2932",
            selectbackground="#f0aeca",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
        )
        self.listbox.pack(fill=Y, expand=True, pady=(8, 0))

        right_panel = Frame(self.canvas, bg="#fff8fb", highlightthickness=1, highlightbackground="#f3c8d5")
        text_frame = Frame(right_panel, bg="#fff8fb")
        text_frame.pack(side=TOP, fill=BOTH, expand=True)
        scroll = Scrollbar(text_frame)
        scroll.pack(side=RIGHT, fill=Y)
        self.chat = Text(
            text_frame,
            wrap="word",
            yscrollcommand=scroll.set,
            bg="#fff8fb",
            fg="#2e2932",
            insertbackground="#2e2932",
            borderwidth=0,
            highlightthickness=0,
            padx=14,
            pady=14,
            font=("Microsoft YaHei UI", 10),
            state="disabled",
            cursor="arrow",
        )
        self.chat.pack(side=LEFT, fill=BOTH, expand=True)
        scroll.config(command=self.chat.yview)
        self.chat.tag_configure("user", foreground="#2774a8", spacing3=8)
        self.chat.tag_configure("agent", foreground="#2e2932", spacing3=10)
        self.chat.tag_configure("meta", foreground="#8d7481", spacing3=6)

        input_frame = Frame(right_panel, bg="#fff3f7", padx=10, pady=10)
        input_frame.pack(side=BOTTOM, fill=X)
        self.entry = Entry(
            input_frame,
            bg="#ffffff",
            fg="#2e2932",
            insertbackground="#2e2932",
            borderwidth=0,
            font=("Microsoft YaHei UI", 11),
        )
        self.entry.pack(side=LEFT, fill=X, expand=True, ipady=8)
        self.entry.bind("<Return>", self.on_send)
        make_button(input_frame, "发送", self.on_send).pack(side=RIGHT, padx=(10, 0))

        self.header_window = self.canvas.create_window(24, 18, window=header, anchor="nw")
        self.left_window = self.canvas.create_window(24, 100, window=left, anchor="nw")
        self.right_window = self.canvas.create_window(384, 100, window=right_panel, anchor="nw")
        self.canvas.bind("<Configure>", self.on_canvas_resize)
        self.on_canvas_resize()

    def apply_window_icons(self) -> None:
        if not TITLE_ICON_FILE.exists() and not TASKBAR_ICON_FILE.exists():
            return
        try:
            if TITLE_ICON_FILE.exists():
                self.root.iconbitmap(default=str(TITLE_ICON_FILE))
            self.root.update_idletasks()
            hwnd = self.root.winfo_id()
            user32 = ctypes.windll.user32
            user32.LoadImageW.restype = ctypes.c_void_p
            user32.SendMessageW.restype = ctypes.c_void_p
            image_icon = 1
            load_from_file = 0x00000010
            wm_seticon = 0x0080
            icon_small = 0
            icon_big = 1
            icon_small2 = 2

            if TITLE_ICON_FILE.exists():
                small_icon = user32.LoadImageW(None, str(TITLE_ICON_FILE), image_icon, 16, 16, load_from_file)
                if small_icon:
                    self.icon_handles.append(small_icon)
                    user32.SendMessageW(hwnd, wm_seticon, icon_small, small_icon)
                    user32.SendMessageW(hwnd, wm_seticon, icon_small2, small_icon)

            if TASKBAR_ICON_FILE.exists():
                big_icon = user32.LoadImageW(None, str(TASKBAR_ICON_FILE), image_icon, 256, 256, load_from_file)
                if not big_icon:
                    big_icon = user32.LoadImageW(None, str(TASKBAR_ICON_FILE), image_icon, 64, 64, load_from_file)
                if big_icon:
                    self.icon_handles.append(big_icon)
                    user32.SendMessageW(hwnd, wm_seticon, icon_big, big_icon)
        except Exception:
            pass

    def on_canvas_resize(self, event=None) -> None:
        width = self.canvas.winfo_width()
        height = self.canvas.winfo_height()
        if width <= 1 or height <= 1:
            return
        if self.background_photo and self.background_item:
            image_width = self.background_photo.width()
            image_height = self.background_photo.height()
            x = (width - image_width) // 2
            y = (height - image_height) // 2
            self.canvas.coords(self.background_item, x, y)
            self.canvas.tag_lower(self.background_item)
        header_width = max(760, width - 48)
        content_height = max(420, height - 124)
        left_width = 340
        gap = 20
        right_x = 24 + left_width + gap
        right_width = max(420, width - right_x - 24)
        if self.header_window:
            self.canvas.itemconfigure(self.header_window, width=header_width, height=64)
        if self.left_window:
            self.canvas.itemconfigure(self.left_window, width=left_width, height=content_height)
        if self.right_window:
            self.canvas.coords(self.right_window, right_x, 100)
            self.canvas.itemconfigure(self.right_window, width=right_width, height=content_height)

    def write_user(self, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert(END, f"\n你 > {text}\n", "user")
        self.chat.configure(state="disabled")
        self.chat.see(END)

    def write_agent(self, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert(END, f"\nAgent > {text}\n", "agent")
        self.chat.configure(state="disabled")
        self.chat.see(END)

    def write_meta(self, text: str) -> None:
        self.chat.configure(state="normal")
        self.chat.insert(END, f"\n{text}\n", "meta")
        self.chat.configure(state="disabled")
        self.chat.see(END)

    def on_send(self, event=None) -> None:
        text = self.entry.get().strip()
        if not text:
            return
        self.entry.delete(0, END)
        self.write_user(text)
        lowered = text.lower()
        if any(word in lowered for word in ["刷新", "更新", "赛程", "比赛", "contest", "schedule"]):
            self.refresh_async()
        elif any(word in lowered for word in ["帮助", "help", "指令"]):
            self.show_help()
        elif any(word in lowered for word in ["提醒", "remind", "闹钟"]):
            if any(word in lowered for word in ["关闭", "关掉", "停止", "off", "disable"]):
                self.reminder_enabled.set(False)
                self.write_agent("已关闭赛前 30 分钟提醒。")
            else:
                self.reminder_enabled.set(True)
                self.write_agent("已开启赛前 30 分钟提醒。程序保持运行时，我会弹窗提醒。")
                self.check_reminders()
        elif any(word in lowered for word in ["导出", "markdown", "md"]):
            self.export_markdown()
        elif lowered.startswith("打开"):
            self.open_selected()
        else:
            self.write_agent("我现在专注做赛程 Agent。可用指令：刷新赛程、打开选中链接、导出 Markdown、提醒、帮助。")

    def show_help(self) -> None:
        self.write_agent(
            "可用指令：\n"
            "- 刷新赛程：拉取未来 7 天 CF / AtCoder / LeetCode / 牛客 / 洛谷。\n"
            "- 打开选中链接：打开左侧选中的比赛页面。\n"
            "- 导出 Markdown：写入 latest_contests.md。\n"
            "- 赛前30分钟提醒：勾选右上角开关后，只要程序运行，会弹窗提醒。\n"
            "- 每小时自动刷新：勾选右上角开关后后台刷新。"
        )

    def refresh_async(self) -> None:
        if self.refreshing:
            self.write_meta("正在刷新中，稍等。")
            return
        self.refreshing = True
        self.write_meta("正在请求官方数据源...")
        thread = threading.Thread(target=self.refresh_worker, daemon=True)
        thread.start()

    def refresh_worker(self) -> None:
        try:
            contests, errors = fetch_all_contests(days=7)
            self.root.after(0, lambda: self.on_refresh_done(contests, errors))
        except Exception:
            err = traceback.format_exc(limit=3)
            self.root.after(0, lambda: self.on_refresh_failed(err))

    def on_refresh_done(self, contests: list[Contest], errors: list[str]) -> None:
        self.refreshing = False
        self.contests = contests
        self.render_contest_list()
        self.write_agent(render_ui_report(contests, errors))
        self.check_reminders()

    def on_refresh_failed(self, err: str) -> None:
        self.refreshing = False
        self.write_agent(f"刷新失败：\n{err}")

    def render_contest_list(self) -> None:
        self.listbox.delete(0, END)
        for item in self.contests:
            self.listbox.insert(END, f"{item.start.strftime('%m/%d %H:%M')} [{item.platform}] {item.title}")

    def open_selected(self) -> None:
        idxs = self.listbox.curselection()
        if not idxs:
            self.write_agent("先在左侧选中一场比赛。")
            return
        contest = self.contests[idxs[0]]
        webbrowser.open(contest.link)
        self.write_agent(f"已打开：{contest.title}")

    def export_markdown(self) -> None:
        md = render_markdown(self.contests)
        LATEST_MD_FILE.write_text(md, encoding="utf-8")
        self.write_agent(f"已导出到：{LATEST_MD_FILE}")

    def schedule_auto_refresh(self) -> None:
        if self.auto_refresh.get() and not self.refreshing:
            self.refresh_async()
        self.root.after(60 * 60 * 1000, self.schedule_auto_refresh)

    def contest_reminder_key(self, contest: Contest) -> str:
        return f"{contest.platform}|{contest.title}|{contest.start.isoformat()}|{contest.link}"

    def schedule_reminders(self) -> None:
        self.check_reminders()
        self.root.after(60 * 1000, self.schedule_reminders)

    def check_reminders(self) -> None:
        if not self.reminder_enabled.get() or not self.contests:
            return
        now = datetime.now(BEIJING)
        due: list[Contest] = []
        for contest in self.contests:
            key = self.contest_reminder_key(contest)
            seconds_until_start = (contest.start - now).total_seconds()
            if 0 <= seconds_until_start <= self.reminder_minutes * 60 and key not in self.reminded_contests:
                self.reminded_contests.add(key)
                due.append(contest)
        if due:
            self.show_reminder(due)

    def show_reminder(self, contests: list[Contest]) -> None:
        lines = []
        for contest in contests:
            minutes_left = max(0, int((contest.start - datetime.now(BEIJING)).total_seconds() // 60))
            lines.append(f"[{contest.platform}] {contest.title}\n{contest.compact_time()}，约 {minutes_left} 分钟后开始")
        text = "\n\n".join(lines)
        self.write_agent(f"⏰ 赛前提醒：\n{text}")
        self.root.bell()
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(1500, lambda: self.root.attributes("-topmost", False))
        messagebox.showinfo("算法竞赛赛前提醒", text)

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ContestAgentApp().run()
