import ctypes
import http.cookiejar
import json
import random
import re
import shutil
import subprocess
import threading
import traceback
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from html import unescape
from pathlib import Path
from tkinter import BOTH, END, LEFT, RIGHT, TOP, BOTTOM, X, Y, BooleanVar, Button, Canvas, Checkbutton, Entry, Frame, Label, Listbox, PhotoImage, Scrollbar, StringVar, Text, Tk, Toplevel, messagebox


APP_DIR = Path(__file__).resolve().parent
CACHE_FILE = APP_DIR / "contest_cache.json"
LATEST_MD_FILE = APP_DIR / "latest_contests.md"
LATEST_ICS_FILE = APP_DIR / "latest_contests.ics"
BACKGROUND_IMAGE_FILE = APP_DIR / "assets" / "background.png"
TITLE_ICON_FILE = APP_DIR / "assets" / "title_icon.ico"
TASKBAR_ICON_FILE = APP_DIR / "assets" / "taskbar_icon.ico"
BEIJING = timezone(timedelta(hours=8), "UTC+8")
USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"


@dataclass
class Contest:
    platform: str
    title: str
    start: datetime
    duration_seconds: int
    link: str
    note: str = "报名状态以平台页面为准"
    original_platform: str = ""

    def __post_init__(self) -> None:
        if not self.original_platform:
            self.original_platform = self.platform

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
            "original_platform": self.original_platform,
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
            original_platform=raw.get("original_platform", raw["platform"]),
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
    total_rows = 0
    for url in urls:
        html = request_text(url)
        raws = re.findall(r'data-json="(.*?)"', html, flags=re.S | re.I)
        total_rows += len(raws)
        for raw in raws:
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
    if total_rows == 0:
        raise ParserStructureError("页面未找到任何 data-json 节点,可能已改版")
    return contests


def parse_clist_time(raw: str) -> datetime:
    return datetime.strptime(raw, "%B %d, %Y %H:%M:%S").replace(tzinfo=timezone.utc).astimezone(BEIJING)


def make_session_opener(*warmup_urls: str) -> urllib.request.OpenerDirector:
    jar = http.cookiejar.CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = [
        ("User-Agent", USER_AGENT),
        ("Accept", "text/html,application/xhtml+xml,application/json"),
        ("Accept-Language", "zh-CN,zh;q=0.9,en;q=0.8"),
    ]
    for url in warmup_urls:
        try:
            opener.open(url, timeout=15).read()
        except Exception:
            pass
    return opener


def fetch_luogu(now: datetime, days: int) -> list[Contest]:
    opener = make_session_opener("https://www.luogu.com.cn/")
    raw = opener.open("https://www.luogu.com.cn/contest/list", timeout=20).read().decode("utf-8", "ignore")
    ctx_match = re.search(
        r'<script id="lentille-context" type="application/json">(.*?)</script>',
        raw,
        flags=re.S,
    )
    if not ctx_match:
        raise ParserStructureError("洛谷页面未找到 lentille-context 节点,可能已改版")
    try:
        payload = json.loads(ctx_match.group(1))
    except json.JSONDecodeError as exc:
        raise ParserStructureError(f"洛谷 lentille-context 不是合法 JSON: {exc}")
    results = payload.get("data", {}).get("contests", {}).get("result", [])
    if not isinstance(results, list):
        raise ParserStructureError("洛谷 contests.result 不是列表,可能已改版")
    contests: list[Contest] = []
    for item in results:
        start_ts = item.get("startTime")
        end_ts = item.get("endTime")
        contest_id = item.get("id")
        title = str(item.get("name", "")).strip()
        if not (start_ts and contest_id and title):
            continue
        start = datetime.fromtimestamp(int(start_ts), timezone.utc).astimezone(BEIJING)
        end = datetime.fromtimestamp(int(end_ts), timezone.utc).astimezone(BEIJING) if end_ts else start
        if not (in_window(start, now, days) or start <= now <= end):
            continue
        duration_seconds = max(0, int((end - start).total_seconds()))
        note = "报名状态以洛谷页面为准"
        if start <= now <= end:
            note = "正在进行;报名状态以洛谷页面为准"
        contests.append(
            Contest(
                platform="洛谷",
                title=title,
                start=start,
                duration_seconds=duration_seconds,
                link=f"https://www.luogu.com.cn/contest/{contest_id}",
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
        raise ParserStructureError("未找到 Upcoming Contests 锚点,可能已改版")
    upcoming = section[1].split("</tbody>", 1)[0]
    rows = re.findall(r"<tr>(.*?)</tr>", upcoming, flags=re.S | re.I)
    if not rows:
        raise ParserStructureError("Upcoming Contests 区块内未找到任何 <tr>")
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


LANQIAO_ALGO_SUBJECTS = {"蓝桥杯", "C++", "C语言", "Java", "Python", "不限语言"}


def fetch_lanqiao(now: datetime, days: int) -> list[Contest]:
    opener = make_session_opener("https://www.lanqiao.cn/")
    raw = opener.open(
        "https://www.lanqiao.cn/api/v2/contests/?page_size=50&page=1",
        timeout=20,
    ).read().decode("utf-8", "ignore")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ParserStructureError(f"蓝桥 API 返回非 JSON: {exc}")
    results = data.get("results")
    if not isinstance(results, list):
        raise ParserStructureError("蓝桥 API 缺少 results 列表,可能已改版")
    contests: list[Contest] = []
    for item in results:
        subject = str(item.get("subject", "")).strip()
        if subject not in LANQIAO_ALGO_SUBJECTS:
            continue
        open_at = item.get("open_at")
        end_at = item.get("end_at")
        if not open_at:
            continue
        try:
            start = datetime.fromisoformat(open_at).astimezone(BEIJING)
            end = datetime.fromisoformat(end_at).astimezone(BEIJING) if end_at else start
        except ValueError:
            continue
        if not (in_window(start, now, days) or start <= now <= end):
            continue
        title = str(item.get("name", "")).strip()
        if not title:
            continue
        html_url = item.get("html_url") or f"/contests/{item.get('id')}/"
        link = "https://www.lanqiao.cn" + html_url if html_url.startswith("/") else html_url
        duration_seconds = max(0, int((end - start).total_seconds()))
        note = f"科目:{subject};报名/入场以蓝桥页面为准"
        if start <= now <= end:
            note = f"正在进行;科目:{subject}"
        contests.append(
            Contest(
                platform="蓝桥",
                title=title,
                start=start,
                duration_seconds=duration_seconds,
                link=link,
                note=note,
            )
        )
    return contests


class ParserStructureError(RuntimeError):
    """HTML 结构与预期不符,可能是站点改版"""


XCPC_PATTERN = re.compile(
    r"(ICPC|CCPC|XCPC|区域赛|省赛|国赛|总决赛|重现赛|同步赛|Regional|Asia\s*Regional|Provincial|程序设计竞赛)",
    re.I,
)


def mark_xcpc(contests: list[Contest]) -> None:
    """命中关键字的比赛重标 platform=XCPC,方便筛选 ACM 选手最关心的正式赛/同步赛"""
    for c in contests:
        if c.platform == "XCPC":
            continue
        if XCPC_PATTERN.search(c.title):
            c.note = f"原平台:{c.platform};{c.note}"
            c.platform = "XCPC"


@dataclass
class RatingInfo:
    platform: str
    handle: str
    current: int | None = None
    max_rating: int | None = None
    rank: str = ""
    recent: list[tuple[str, int, int]] = None  # (contest_name, old, new)
    error: str = ""

    def __post_init__(self):
        if self.recent is None:
            self.recent = []


def fetch_cf_rating(handle: str) -> RatingInfo:
    info = RatingInfo(platform="Codeforces", handle=handle)
    try:
        data = request_json(f"https://codeforces.com/api/user.info?handles={urllib.parse.quote(handle)}")
        if data.get("status") != "OK" or not data.get("result"):
            info.error = "用户不存在或 API 失败"
            return info
        u = data["result"][0]
        info.current = u.get("rating")
        info.max_rating = u.get("maxRating")
        info.rank = u.get("rank", "")
        hist = request_json(f"https://codeforces.com/api/user.rating?handle={urllib.parse.quote(handle)}")
        if hist.get("status") == "OK":
            for h in hist["result"][-5:]:
                info.recent.append((h.get("contestName", "")[:36], int(h.get("oldRating", 0)), int(h.get("newRating", 0))))
            info.recent.reverse()
    except Exception as exc:
        info.error = str(exc)
    return info


def fetch_atcoder_rating(handle: str) -> RatingInfo:
    info = RatingInfo(platform="AtCoder", handle=handle)
    try:
        data = request_json(f"https://atcoder.jp/users/{urllib.parse.quote(handle)}/history/json")
        if not isinstance(data, list) or not data:
            info.error = "用户不存在或无 rating 记录"
            return info
        rated = [d for d in data if d.get("IsRated")]
        if rated:
            info.current = rated[-1].get("NewRating")
            info.max_rating = max(d.get("NewRating", 0) for d in rated)
            for d in rated[-5:]:
                info.recent.append((d.get("ContestName", "")[:36], int(d.get("OldRating", 0)), int(d.get("NewRating", 0))))
            info.recent.reverse()
    except Exception as exc:
        info.error = str(exc)
    return info


def fetch_all_ratings(handles: dict[str, str]) -> list[RatingInfo]:
    fetchers = {
        "cf": fetch_cf_rating,
        "atcoder": fetch_atcoder_rating,
    }
    out: list[RatingInfo] = []
    with ThreadPoolExecutor(max_workers=max(1, len(handles))) as pool:
        futures = {pool.submit(fetchers[k], v): k for k, v in handles.items() if k in fetchers and v}
        for fut in as_completed(futures):
            try:
                out.append(fut.result())
            except Exception as exc:
                out.append(RatingInfo(platform=futures[fut], handle=handles.get(futures[fut], "?"), error=str(exc)))
    out.sort(key=lambda x: x.platform)
    return out


def format_rating_panel(infos: list[RatingInfo]) -> str:
    if not infos:
        return "尚未配置 handle。点击「我的 Rating」按钮填入 CF / AtCoder 用户名。"
    lines = ["📊 个人 Rating 跟踪", ""]
    for info in infos:
        lines.append(f"[{info.platform}] {info.handle}")
        if info.error:
            lines.append(f"  ⚠ {info.error}")
        else:
            cur = info.current if info.current is not None else "未参赛"
            mx = info.max_rating if info.max_rating is not None else "-"
            rank = f" ({info.rank})" if info.rank else ""
            lines.append(f"  当前 {cur}  历史最高 {mx}{rank}")
            if info.recent:
                lines.append("  近期:")
                for name, old, new in info.recent:
                    delta = new - old
                    sign = "+" if delta >= 0 else ""
                    lines.append(f"    {old:>4} → {new:<4} ({sign}{delta:+d})  {name}")
        lines.append("")
    return "\n".join(lines)


def recommend_cf_problems(handle: str, target_rating: int | None, count: int = 3) -> tuple[list[dict], str]:
    """基于 rating ±200 推荐 CF 题,排除该 handle 已 AC。返回 (题目列表, 备注)"""
    user_info = request_json(f"https://codeforces.com/api/user.info?handles={urllib.parse.quote(handle)}")
    if user_info.get("status") != "OK" or not user_info.get("result"):
        raise RuntimeError(f"CF handle {handle} 不存在")
    rating = user_info["result"][0].get("rating") or target_rating or 1200
    lo, hi = rating - 100, rating + 200
    status = request_json(f"https://codeforces.com/api/user.status?handle={urllib.parse.quote(handle)}")
    solved: set[tuple[int, str]] = set()
    if status.get("status") == "OK":
        for sub in status["result"]:
            if sub.get("verdict") == "OK":
                p = sub["problem"]
                if p.get("contestId") and p.get("index"):
                    solved.add((int(p["contestId"]), p["index"]))
    pool = request_json("https://codeforces.com/api/problemset.problems")
    candidates = []
    if pool.get("status") == "OK":
        for p in pool["result"]["problems"]:
            r = p.get("rating")
            cid, idx = p.get("contestId"), p.get("index")
            if r and lo <= r <= hi and cid and idx and (cid, idx) not in solved:
                candidates.append(p)
    random.shuffle(candidates)
    note = f"基于 {handle} 当前 rating {rating},推荐范围 [{lo}, {hi}],已排除 {len(solved)} 道 AC 题"
    return candidates[:count], note


def format_recommendation(problems: list[dict], note: str) -> str:
    if not problems:
        return f"⚠ 推荐池为空。\n{note}"
    lines = ["📝 今日 CF 推荐", "", note, ""]
    for i, p in enumerate(problems, 1):
        tags = ", ".join(p.get("tags", [])[:4]) or "-"
        link = f"https://codeforces.com/problemset/problem/{p['contestId']}/{p['index']}"
        lines.append(f"{i}. [{p.get('rating')}] {p['name']}")
        lines.append(f"   标签: {tags}")
        lines.append(f"   链接: {link}")
        lines.append("")
    return "\n".join(lines)


def fetch_all_contests(days: int = 7) -> tuple[list[Contest], list[str]]:
    now = datetime.now(BEIJING)
    sources = [
        ("Codeforces", "CF", fetch_codeforces),
        ("LeetCode", "LeetCode", fetch_leetcode),
        ("AtCoder", "ATC", fetch_atcoder),
        ("牛客", "牛客", fetch_nowcoder),
        ("洛谷", "洛谷", fetch_luogu),
        ("蓝桥", "蓝桥", fetch_lanqiao),
    ]
    fresh: list[Contest] = []
    errors: list[str] = []
    failed_platforms: set[str] = set()
    with ThreadPoolExecutor(max_workers=len(sources)) as pool:
        future_to_info = {pool.submit(func, now, days): (name, platform) for name, platform, func in sources}
        for future in as_completed(future_to_info):
            name, platform = future_to_info[future]
            try:
                fresh.extend(future.result())
            except Exception as exc:
                errors.append(f"{name}: {exc}")
                failed_platforms.add(platform)
    if failed_platforms:
        cached, _ = load_cache()
        for c in cached:
            if c.original_platform in failed_platforms and c.start >= now:
                if not c.note.startswith("[上次缓存]"):
                    c.note = f"[上次缓存] {c.note}"
                fresh.append(c)
    mark_xcpc(fresh)
    fresh.sort(key=lambda item: item.start)
    save_cache(fresh)
    return fresh, errors


def _read_cache_raw() -> dict:
    if not CACHE_FILE.exists():
        return {}
    try:
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_cache(contests: list[Contest]) -> None:
    payload = _read_cache_raw()
    payload["updated_at"] = datetime.now(BEIJING).isoformat()
    payload["contests"] = [item.to_json() for item in contests]
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def load_cache() -> tuple[list[Contest], str | None]:
    payload = _read_cache_raw()
    if not payload:
        return [], None
    try:
        now = datetime.now(BEIJING)
        contests = [
            c for c in (Contest.from_json(item) for item in payload.get("contests", []))
            if c.start >= now - timedelta(days=14)
        ]
        return contests, payload.get("updated_at")
    except Exception:
        return [], None


def split_past_future(contests: list[Contest]) -> tuple[list[Contest], list[Contest]]:
    now = datetime.now(BEIJING)
    future = [c for c in contests if c.start >= now - timedelta(hours=2)]
    past = [c for c in contests if now - timedelta(days=14) <= c.start < now - timedelta(hours=2)]
    past.sort(key=lambda c: c.start, reverse=True)
    return future, past


def load_prefs() -> dict:
    return _read_cache_raw().get("prefs", {})


def save_prefs(prefs: dict) -> None:
    payload = _read_cache_raw()
    payload["prefs"] = prefs
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


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
    lines.append("数据源：Codeforces 官方 API、LeetCode 官方 GraphQL、AtCoder 官方赛程、牛客公开列表、洛谷官方页、蓝桥云课公开 API（仅算法相关科目）。")
    return "\n".join(lines)


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(",", "\\,").replace(";", "\\;").replace("\n", "\\n")


def _ics_fold(line: str) -> str:
    encoded = line.encode("utf-8")
    if len(encoded) <= 75:
        return line
    chunks: list[str] = []
    i = 0
    while i < len(encoded):
        chunk = encoded[i:i + 73]
        while chunk and (chunk[-1] & 0xC0) == 0x80:
            chunk = chunk[:-1]
        if not chunk:
            chunk = encoded[i:i + 73]
        chunks.append(chunk.decode("utf-8", "ignore"))
        i += len(chunk)
    return chunks[0] + "".join("\r\n " + c for c in chunks[1:])


def render_ics(contests: list[Contest]) -> str:
    now_utc = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out: list[str] = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//local//Algorithm Contest Scheduler Agent//ZH",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
        "X-WR-CALNAME:算法竞赛日程",
        "X-WR-TIMEZONE:Asia/Shanghai",
    ]
    for c in contests:
        start_utc = c.start.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        end = c.start + timedelta(seconds=max(c.duration_seconds, 60))
        end_utc = end.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        uid = f"{abs(hash((c.platform, c.title, c.start.isoformat())))}@local.contest.agent"
        out.append("BEGIN:VEVENT")
        out.append(_ics_fold(f"UID:{uid}"))
        out.append(f"DTSTAMP:{now_utc}")
        out.append(f"DTSTART:{start_utc}")
        out.append(f"DTEND:{end_utc}")
        out.append(_ics_fold(f"SUMMARY:[{c.platform}] {_ics_escape(c.title)}"))
        out.append(_ics_fold(f"DESCRIPTION:{_ics_escape(c.note)}\\n{_ics_escape(c.link)}"))
        out.append(_ics_fold(f"URL:{c.link}"))
        out.append("BEGIN:VALARM")
        out.append("TRIGGER:-PT30M")
        out.append("ACTION:DISPLAY")
        out.append(_ics_fold(f"DESCRIPTION:30 分钟后开始: [{c.platform}] {_ics_escape(c.title)}"))
        out.append("END:VALARM")
        out.append("END:VEVENT")
    out.append("END:VCALENDAR")
    return "\r\n".join(out) + "\r\n"


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
    lines.append("数据源：Codeforces 官方 API、LeetCode 官方 GraphQL、AtCoder 官方赛程、牛客公开列表、洛谷官方页、蓝桥云课公开 API。")
    lines.append("蓝桥仅保留算法相关科目（蓝桥杯/C++/C/Java/Python/不限语言），Web/AI 等不输出。")
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


_POWERSHELL_PATH: str | None = None


def _powershell() -> str | None:
    global _POWERSHELL_PATH
    if _POWERSHELL_PATH is None:
        _POWERSHELL_PATH = shutil.which("powershell.exe") or shutil.which("pwsh.exe") or ""
    return _POWERSHELL_PATH or None


def send_toast(title: str, body: str) -> bool:
    """用 Windows 10/11 Toast 推送通知,返回是否成功。失败时调用方应回退到 messagebox。"""
    ps = _powershell()
    if not ps:
        return False
    def _esc(s: str) -> str:
        return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("'", "&apos;")
    script = (
        "[Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null;"
        "[Windows.Data.Xml.Dom.XmlDocument,Windows.Data.Xml.Dom.XmlDocument,ContentType=WindowsRuntime] | Out-Null;"
        f"$xml = '<toast><visual><binding template=\"ToastGeneric\"><text>{_esc(title)}</text><text>{_esc(body)}</text></binding></visual></toast>';"
        "$doc = New-Object Windows.Data.Xml.Dom.XmlDocument;"
        "$doc.LoadXml($xml);"
        "$toast = [Windows.UI.Notifications.ToastNotification]::new($doc);"
        "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('local.contest.scheduler.agent').Show($toast);"
    )
    try:
        completed = subprocess.run(
            [ps, "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden", "-Command", script],
            capture_output=True, timeout=8, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return completed.returncode == 0
    except Exception:
        return False


class ContestAgentApp:
    def __init__(self) -> None:
        configure_windows_app_id()
        self.root = Tk()
        self.root.title("Algorithm Contest Scheduler Agent")
        self.root.geometry("1320x820")
        self.root.minsize(1180, 720)
        self.root.configure(bg="#f6dce5")

        self.contests: list[Contest] = []
        self.past_contests: list[Contest] = []
        prefs = load_prefs()
        self.auto_refresh = BooleanVar(value=bool(prefs.get("auto_refresh", False)))
        self.reminder_enabled = BooleanVar(value=bool(prefs.get("reminder_enabled", True)))
        self.auto_refresh.trace_add("write", lambda *_: self.persist_prefs())
        self.reminder_enabled.trace_add("write", lambda *_: self.persist_prefs())
        self.platform_filters: dict[str, BooleanVar] = {}
        saved_filters = prefs.get("platform_filters", {})
        for platform in ["CF", "ATC", "LeetCode", "牛客", "洛谷", "蓝桥", "XCPC"]:
            var = BooleanVar(value=bool(saved_filters.get(platform, True)))
            var.trace_add("write", lambda *_: self.on_filter_change())
            self.platform_filters[platform] = var
        self.handles: dict[str, str] = dict(prefs.get("handles", {}))
        self.visible_index_map: list[int] = []
        self.refreshing = False
        self.reminder_minutes = 30
        self.reminded_contests: set[str] = set()
        self.last_refresh_at: datetime | None = None
        self.background_photo: PhotoImage | None = None
        self.background_item: int | None = None
        self.header_window: int | None = None
        self.left_window: int | None = None
        self.right_window: int | None = None
        self.icon_handles: list[int] = []

        self.build_ui()
        self.apply_window_icons()
        cached, updated_at = load_cache()
        cache_is_fresh = False
        if updated_at:
            try:
                cache_age = datetime.now(BEIJING) - datetime.fromisoformat(updated_at)
                cache_is_fresh = cache_age < timedelta(hours=1)
            except ValueError:
                cache_is_fresh = False
        if cached:
            future, past = split_past_future(cached)
            self.contests = future
            self.past_contests = past
            self.write_agent(f"已加载缓存赛程（{updated_at or '未知时间'}）。")
            self.render_contest_list()
            self.write_agent(render_ui_report(self.contests))
        else:
            self.write_agent("我是算法竞赛日程管家。输入“刷新赛程”或点击右上角按钮，我会拉取未来 7 天赛事。")
        if not cache_is_fresh:
            self.write_meta("缓存已过期或为空，正在后台刷新...")
            self.refresh_async()
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
                padx=9,
                pady=6,
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
        make_button(header, "刷新", self.refresh_async).pack(side=RIGHT, padx=4)
        make_button(header, "打开", self.open_selected).pack(side=RIGHT, padx=4)
        make_button(header, "MD", self.export_markdown).pack(side=RIGHT, padx=4)
        make_button(header, "ICS", self.export_ics).pack(side=RIGHT, padx=4)
        make_button(header, "Rating", self.show_rating).pack(side=RIGHT, padx=4)
        make_button(header, "推荐", self.show_recommendation).pack(side=RIGHT, padx=4)

        left = Frame(self.canvas, bg="#fff8fb", padx=10, pady=10, highlightthickness=1, highlightbackground="#f3c8d5")
        Label(left, text="未来 7 天", fg="#6c5664", bg="#fff8fb", font=("Microsoft YaHei UI", 10, "bold")).pack(anchor="w")
        filter_row = Frame(left, bg="#fff8fb")
        filter_row.pack(anchor="w", pady=(4, 0), fill=X)
        for i, platform in enumerate(self.platform_filters):
            Checkbutton(
                filter_row,
                text=platform,
                variable=self.platform_filters[platform],
                fg="#524955",
                bg="#fff8fb",
                activebackground="#fff8fb",
                activeforeground="#2b2730",
                selectcolor="#fff8fb",
                font=("Microsoft YaHei UI", 8),
                padx=0,
                pady=0,
            ).grid(row=i // 4, column=i % 4, sticky="w", padx=(0, 4))
        list_wrap = Frame(left, bg="#fff8fb")
        list_wrap.pack(fill=BOTH, expand=True, pady=(8, 0))
        list_hscroll = Scrollbar(list_wrap, orient="horizontal")
        list_hscroll.pack(side=BOTTOM, fill=X)
        list_vscroll = Scrollbar(list_wrap, orient="vertical")
        list_vscroll.pack(side=RIGHT, fill=Y)
        self.listbox = Listbox(
            list_wrap,
            width=42,
            bg="#fff8fb",
            fg="#2e2932",
            selectbackground="#f0aeca",
            selectforeground="#ffffff",
            activestyle="none",
            borderwidth=0,
            highlightthickness=0,
            font=("Consolas", 10),
            xscrollcommand=list_hscroll.set,
            yscrollcommand=list_vscroll.set,
        )
        self.listbox.pack(side=LEFT, fill=BOTH, expand=True)
        list_hscroll.config(command=self.listbox.xview)
        list_vscroll.config(command=self.listbox.yview)
        self.listbox.bind("<Double-Button-1>", lambda _e: self.open_selected())
        self.listbox.bind("<Return>", lambda _e: self.open_selected())

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
        left_width = 380
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
        elif any(word in lowered for word in ["日历", "ics", "calendar"]):
            self.export_ics()
        elif any(word in lowered for word in ["导出", "markdown", "md"]):
            self.export_markdown()
        elif any(word in lowered for word in ["rating", "分数", "积分", "我的分"]):
            self.show_rating()
        elif any(word in lowered for word in ["推荐", "题目", "刷题", "recommend"]):
            self.show_recommendation()
        elif any(word in lowered for word in ["handle", "用户名", "配置"]):
            self.open_handles_dialog()
        elif lowered.startswith("打开"):
            self.open_selected()
        else:
            self.write_agent("输入「帮助」查看完整指令。常用：刷新赛程 / 我的 rating / 推荐 / 导出日历 / 导出 markdown。")

    def show_help(self) -> None:
        self.write_agent(
            "可用指令 / 按钮：\n"
            "- 刷新赛程：拉取未来 7 天 CF / AtCoder / LeetCode / 牛客 / 洛谷 / 蓝桥。\n"
            "- 打开选中链接：打开左侧选中的比赛页面（也可双击或 Enter）。\n"
            "- 导出 Markdown：写入 latest_contests.md。\n"
            "- 导出日历：写入 latest_contests.ics，可订阅到手机/Outlook/Google 日历。\n"
            "- 我的 Rating：填入 CF / AtCoder handle 后展示当前分、历史最高、近 5 场 Δ。\n"
            "- 每日推荐：基于 CF rating 推 3 题（已过滤 AC）。\n"
            "- 平台筛选：左侧 checkbox 控制 listbox 显示的平台（含 XCPC 同步赛/重现赛）。\n"
            "- 赛前30分钟提醒：勾选后会发 Windows Toast 通知（失败回退弹窗）。\n"
            "- 每小时自动刷新：勾选后后台保持最新（避免与手动刷新撞车）。\n"
            "- ⚠ 标记表示同时段比赛冲突；列表底部「补题区」展示 14 天内已结束的赛事。"
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
        self.last_refresh_at = datetime.now(BEIJING)
        now = datetime.now(BEIJING)
        old_past = list(self.past_contests)
        for c in self.contests:
            if c.start < now - timedelta(hours=2):
                old_past.append(c)
        seen_keys: set[str] = set()
        merged_past: list[Contest] = []
        for c in old_past:
            key = f"{c.platform}|{c.title}|{c.start.isoformat()}"
            if key in seen_keys:
                continue
            seen_keys.add(key)
            if now - timedelta(days=14) <= c.start < now - timedelta(hours=2):
                merged_past.append(c)
        merged_past.sort(key=lambda x: x.start, reverse=True)
        self.past_contests = merged_past
        self.contests = contests
        self.render_contest_list()
        self.write_agent(render_ui_report(contests, errors))
        self.check_reminders()

    def on_refresh_failed(self, err: str) -> None:
        self.refreshing = False
        self.write_agent(f"刷新失败：\n{err}")

    def visible_contests(self) -> list[Contest]:
        return [c for c in self.contests if self.platform_filters.get(c.platform) is None or self.platform_filters[c.platform].get()]

    def conflict_set(self, contests: list[Contest]) -> set[int]:
        sorted_idx = sorted(range(len(contests)), key=lambda i: contests[i].start)
        conflict: set[int] = set()
        for a_pos in range(len(sorted_idx)):
            ia = sorted_idx[a_pos]
            ca = contests[ia]
            a_end = ca.start + timedelta(seconds=ca.duration_seconds)
            for b_pos in range(a_pos + 1, len(sorted_idx)):
                ib = sorted_idx[b_pos]
                cb = contests[ib]
                if cb.start >= a_end:
                    break
                conflict.add(ia)
                conflict.add(ib)
        return conflict

    def render_contest_list(self) -> None:
        self.listbox.delete(0, END)
        self.visible_index_map = []
        visible = self.visible_contests()
        conflicts = self.conflict_set(visible)
        for i, item in enumerate(visible):
            marker = "⚠ " if i in conflicts else "  "
            original = self.contests.index(item)
            self.visible_index_map.append(original)
            self.listbox.insert(END, f"{marker}{item.start.strftime('%m/%d %H:%M')} [{item.platform}] {item.title}")
        past_visible = [
            c for c in self.past_contests
            if self.platform_filters.get(c.platform) is None or self.platform_filters[c.platform].get()
        ]
        if past_visible:
            self.listbox.insert(END, "─── 补题区(14 天内已结束) ───")
            self.visible_index_map.append(-1)
            for item in past_visible:
                self.visible_index_map.append(-2 - self.past_contests.index(item))
                self.listbox.insert(END, f"  {item.start.strftime('%m/%d %H:%M')} [{item.platform}] {item.title}")

    def open_selected(self) -> None:
        idxs = self.listbox.curselection()
        if not idxs:
            self.write_agent("先在左侧选中一场比赛。")
            return
        if not self.visible_index_map or idxs[0] >= len(self.visible_index_map):
            return
        mapped = self.visible_index_map[idxs[0]]
        if mapped == -1:
            return
        if mapped < -1:
            contest = self.past_contests[-(mapped + 2)]
        else:
            contest = self.contests[mapped]
        webbrowser.open(contest.link)
        self.write_agent(f"已打开：{contest.title}")

    def export_markdown(self) -> None:
        md = render_markdown(self.contests)
        LATEST_MD_FILE.write_text(md, encoding="utf-8")
        self.write_agent(f"已导出到：{LATEST_MD_FILE}")

    def export_ics(self) -> None:
        if not self.contests:
            self.write_agent("还没有赛事数据，先刷新一下。")
            return
        ics = render_ics(self.contests)
        LATEST_ICS_FILE.write_bytes(ics.encode("utf-8"))
        self.write_agent(
            f"已导出 iCalendar：{LATEST_ICS_FILE}\n"
            "→ 双击可加到 Outlook；放到云盘公开链接后,Google/Apple/手机日历都可订阅。"
        )

    def open_handles_dialog(self) -> None:
        dlg = Toplevel(self.root)
        dlg.title("配置 handle")
        dlg.configure(bg="#fff8fb")
        dlg.transient(self.root)
        dlg.resizable(False, False)
        rows = [("cf", "Codeforces"), ("atcoder", "AtCoder")]
        entries: dict[str, Entry] = {}
        Label(dlg, text="填写你的 handle(用户名),留空表示不查询", bg="#fff8fb", fg="#524955",
              font=("Microsoft YaHei UI", 9)).grid(row=0, column=0, columnspan=2, padx=12, pady=(10, 6), sticky="w")
        for i, (key, label) in enumerate(rows, start=1):
            Label(dlg, text=label, bg="#fff8fb", fg="#2e2932",
                  font=("Microsoft YaHei UI", 10)).grid(row=i, column=0, padx=12, pady=6, sticky="e")
            e = Entry(dlg, width=24, font=("Microsoft YaHei UI", 10))
            e.insert(0, self.handles.get(key, ""))
            e.grid(row=i, column=1, padx=(0, 12), pady=6, sticky="w")
            entries[key] = e

        def on_save() -> None:
            for key, e in entries.items():
                value = e.get().strip()
                if value:
                    self.handles[key] = value
                else:
                    self.handles.pop(key, None)
            self.persist_prefs()
            dlg.destroy()
            self.show_rating()

        Button(dlg, text="保存并查询", command=on_save, bg="#f5c6d6", fg="#29242b",
               activebackground="#f0aeca", relief="flat", padx=12, pady=6,
               font=("Microsoft YaHei UI", 9, "bold"), cursor="hand2").grid(
            row=len(rows) + 1, column=0, columnspan=2, padx=12, pady=(8, 12))
        dlg.update_idletasks()
        x = self.root.winfo_x() + (self.root.winfo_width() - dlg.winfo_width()) // 2
        y = self.root.winfo_y() + (self.root.winfo_height() - dlg.winfo_height()) // 2
        dlg.geometry(f"+{x}+{y}")

    def show_rating(self) -> None:
        if not self.handles:
            self.write_agent("还没有配置 handle,弹窗已打开。")
            self.open_handles_dialog()
            return
        self.write_meta("正在查询 Rating...")
        def worker() -> None:
            infos = fetch_all_ratings(self.handles)
            self.root.after(0, lambda: self.write_agent(format_rating_panel(infos)))
        threading.Thread(target=worker, daemon=True).start()

    def show_recommendation(self) -> None:
        cf_handle = self.handles.get("cf")
        if not cf_handle:
            self.write_agent("先配置 Codeforces handle。")
            self.open_handles_dialog()
            return
        self.write_meta(f"正在拉取 {cf_handle} 的题目池...")
        def worker() -> None:
            try:
                problems, note = recommend_cf_problems(cf_handle, None)
                msg = format_recommendation(problems, note)
            except Exception as exc:
                msg = f"推荐失败: {exc}"
            self.root.after(0, lambda: self.write_agent(msg))
        threading.Thread(target=worker, daemon=True).start()

    def persist_prefs(self) -> None:
        save_prefs({
            "auto_refresh": bool(self.auto_refresh.get()),
            "reminder_enabled": bool(self.reminder_enabled.get()),
            "platform_filters": {p: v.get() for p, v in self.platform_filters.items()},
            "handles": self.handles,
        })

    def on_filter_change(self) -> None:
        self.persist_prefs()
        if hasattr(self, "listbox"):
            self.render_contest_list()

    def schedule_auto_refresh(self) -> None:
        if self.auto_refresh.get() and not self.refreshing:
            need_refresh = (
                self.last_refresh_at is None
                or (datetime.now(BEIJING) - self.last_refresh_at) >= timedelta(minutes=55)
            )
            if need_refresh:
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
        active_keys = {self.contest_reminder_key(c) for c in self.contests if c.start >= now - timedelta(hours=1)}
        self.reminded_contests &= active_keys
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
        title_first = contests[0].title if len(contests) == 1 else f"{len(contests)} 场即将开始"
        toast_body = f"[{contests[0].platform}] {title_first}" if len(contests) == 1 else text.split("\n\n")[0]

        def notify() -> None:
            ok = send_toast(f"赛前 {self.reminder_minutes} 分钟", toast_body)
            if not ok:
                self.root.after(0, lambda: messagebox.showinfo("算法竞赛赛前提醒", text))

        threading.Thread(target=notify, daemon=True).start()

    def run(self) -> None:
        self.root.mainloop()


if __name__ == "__main__":
    ContestAgentApp().run()
