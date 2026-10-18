# 追剧助手 · 纯逻辑层
# 从桌面版 Rust 实现（model.rs / core.rs）移植：
# 剧名清洗、集数文本解析、排播文案解析、候选过滤、排播规律推算、推送文案构建。
# 本模块不依赖宿主、不访问网络，可在任意环境单测。

import re
from datetime import date, datetime, timedelta

WEEK_NAMES = ["", "周一", "周二", "周三", "周四", "周五", "周六", "周日"]

# 平台代码 -> 中文名
PLATFORM_NAMES = {
    "iqiyi": "爱奇艺",
    "tencent": "腾讯视频",
    "youku": "优酷",
    "bilibili": "B站",
    "mango": "芒果TV",
}

# 平台别名 -> 代码（接受中文名/英文代码等多种写法）
PLATFORM_ALIASES = {
    "爱奇艺": "iqiyi", "iqiyi": "iqiyi", "iqy": "iqiyi", "aiqiyi": "iqiyi",
    "腾讯视频": "tencent", "腾讯": "tencent", "tencent": "tencent", "qq": "tencent",
    "优酷": "youku", "酷喵": "youku", "youku": "youku", "yk": "youku",
    "b站": "bilibili", "bilibili": "bilibili", "bili": "bilibili", "哔哩哔哩": "bilibili", "番剧": "bilibili",
    "芒果tv": "mango", "芒果": "mango", "mango": "mango", "mgtv": "mango",
}


def parse_platform(s: str) -> str | None:
    """中文名/英文别名统一解析为平台代码。"""
    t = str(s or "").strip().lower().replace("（", "").replace("）", "")
    return PLATFORM_ALIASES.get(t)


def https_url(u: str) -> str:
    """封面 URL 统一升级 https，避免混合内容被拦。"""
    u = u or ""
    if u.startswith("http://"):
        return "https://" + u[len("http://"):]
    return u


def clean_title(raw: str) -> str:
    """去掉搜索接口返回的 HTML 高亮标签与平台后缀噪声，便于比对与展示。"""
    t = re.sub(r"<[^>]*>", "", raw or "").strip()
    suffixes = ["电视剧", "高清视频", "高清完整版", "在线观看", "全集", "（中国大陆）", "(中国大陆)"]
    changed = True
    while changed:
        changed = False
        for suf in suffixes:
            if t.endswith(suf):
                t = t[: -len(suf)].strip()
                changed = True
    return t


def show_key(platform: str, name: str) -> str:
    """订阅唯一键：平台 + 清洗后剧名。"""
    return f"{platform}::{clean_title(name)}"


def progress_text(current: int, total: int, finished: bool) -> str:
    if finished or (total > 0 and current >= total and current > 0):
        return f"全{current}集"
    if total > 0:
        return f"更新至{current}/{total}集"
    if current > 0:
        return f"更新至{current}集"
    return "未知"


# ---------- 集数文本解析 ----------

_NUM_NEAR_RE = re.compile(r"\d+")


def parse_episode_text(text: str) -> tuple[int, int, bool]:
    """
    解析集数描述，返回 (当前集, 总集数, 是否完结)。

    - "全36集" / "36集全" / "76集全(完结)" -> (36, 36, True)
    - "更新至52集" -> (52, 0, False)
    只取紧邻「集」字的数字，避免把 2024 之类的年份数字误当集数。
    """
    t = (text or "").strip()
    if not t:
        return 0, 0, False
    finished = ("全" in t) or ("完结" in t)

    nums: list[int] = []
    for m in _NUM_NEAR_RE.finditer(t):
        n = int(m.group())
        if n <= 0:
            continue
        # 数字结束位置前后 6 字符内是否有「集」字
        end = m.end()
        window = t[end:end + 6]
        before = t[max(0, end - 6):end]
        if ("集" in window) or (before.endswith("集")):
            nums.append(n)

    if t.startswith("更新至") or "更新至" in t:
        if nums:
            return nums[0], 0, False
    if finished and nums:
        return nums[0], nums[0], True
    return 0, 0, False


# ---------- 排播文案解析 ----------

def _first_clock(t: str) -> str:
    """取文案里第一个钟点："18:00" / "19点30分" / "20点"；分钟只认 00/30。"""
    m = re.search(r"(\d{1,2}):(\d{2})", t) or re.search(r"(\d{1,2})：(\d{2})", t)
    if m:
        hh, mm = int(m.group(1)), m.group(2)
        if hh <= 23 and mm in ("00", "30"):
            return f"{hh:02d}:{mm}"
        if hh <= 23 and mm in ("20", "50"):
            # 个别平台文案出现 18:20/19:50 等分钟，就近归整为 00/30 档
            return f"{hh:02d}:30" if int(mm) >= 30 else f"{hh:02d}:00"
    m = re.search(r"(\d{1,2})点(30分|半)?", t)
    if m:
        hh = int(m.group(1))
        if hh <= 23:
            return f"{hh:02d}:30" if m.group(2) else f"{hh:02d}:00"
    return ""


def parse_schedule(text: str) -> dict | None:
    """
    解析平台排播文案，返回 {"daily": bool, "weekdays": [1-7], "time": "18:00"}。

    - "VIP会员每日18:00更新" -> daily + 18:00
    - "每周三20:00更新2集" / "每周一、周二20点更新" -> weekdays
    - 多档文案（"VIP每日19:30…非会员每日21:30…"）取第一个时刻（VIP 档）
    """
    t = (text or "").strip()
    if not t:
        return None
    daily = any(k in t for k in ("每日", "每天", "日更", "天天"))

    weekdays: list[int] = []
    pos = t.find("每周")
    if pos >= 0:
        # 「每周」后 10 字符窗口内连续吃星期字，允许「每周一、周二」「每周四 周五」写法
        tail = t[pos + 2: pos + 12]
        wd_map = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7, "天": 7}
        for c in tail:
            if c in wd_map:
                if wd_map[c] not in weekdays:
                    weekdays.append(wd_map[c])
            elif c in "、，， ,周":
                continue
            else:
                break

    if not daily and not weekdays:
        return None
    return {"daily": daily, "weekdays": weekdays, "time": _first_clock(t)}


# ---------- 候选过滤 ----------

_EXTRA_WORDS = (
    "花絮", "预告", "片花", "抢先", "幕后", "原著", "解说", "速看",
    "盘点", "混剪", "纯剪", "专访", "发布会", "彩蛋", "独家",
    "解读", "解析", "揭秘", "剧透", "名场面", "前瞻", "看点",
    "高燃", "直拍", "reaction", "Reaction", "剪辑", "二创",
)

_EPISODE_REF_RE = re.compile(r"第\d+集")


def is_extra_title(title: str) -> bool:
    """花絮/预告/解说类标题特征——正剧标题不含这些词，也不带「第X集」单集引用。"""
    t = title or ""
    return any(k in t for k in _EXTRA_WORDS) or bool(_EPISODE_REF_RE.search(t))


def keep_candidate(cand: dict, query: str = "") -> bool:
    """
    候选保留判断：正剧必有集数信息，且标题不带花絮/切片/解说特征。
    无集数一律剔除——包括标题与剧名同名的单集切片；未开播的剧没有集数可追。
    注意解说类条目常冒充集数（「全36集」徽章），关键词黑名单必须独立于集数规则。
    """
    t = clean_title(cand.get("title", ""))
    return (cand.get("current", 0) > 0 or cand.get("finished", False)) and not is_extra_title(t)


def title_match_rank(title: str, query: str) -> int:
    """标题与查询名的匹配度：0 精确 / 1 前缀（含「第二季」等后缀） / 2 包含 / 3 其他。"""
    t, q = clean_title(title), clean_title(query)
    if t == q:
        return 0
    if q and t.startswith(q):
        return 1
    if (q in t) or (t in q):
        return 2
    return 3


def pick(query: str, candidates: list[dict]) -> dict | None:
    """从候选中挑与查询名最匹配的一条：先精确/包含匹配，再退而取第一条。"""
    if not candidates:
        return None
    return sorted(candidates, key=lambda c: title_match_rank(c.get("title", ""), query))[0]


# ---------- 排播规律推算 ----------

def schedule_pattern(rec: dict) -> tuple[bool, list[int], str]:
    """
    统一排播规律：官方文案优先（爱奇艺 updateStrategy），历史更新日兜底。
    返回 (daily, weekdays, clock)。历史规律至少在 2 个不同星期观察到更新才采信。
    """
    if not rec.get("finished"):
        s = parse_schedule(rec.get("schedule", ""))
        if s:
            if s["daily"]:
                return True, [], s["time"]
            if s["weekdays"]:
                return False, s["weekdays"], s["time"]
            if s["time"]:
                return True, [], s["time"]
        # 历史规律：history = {date_str: 集数}
        hits: list[int] = []
        prev = None
        for d in sorted(rec.get("history", {})):
            v = rec["history"][d]
            if prev is not None and v > prev:
                w = date.fromisoformat(d).isoweekday()
                if w not in hits:
                    hits.append(w)
            prev = v
        if len(hits) >= 2:
            return False, hits, ""
    return False, [], ""


def _clock_passed(clock: str, now: datetime) -> bool | None:
    """当前时间是否已过 "HH:MM" 钟点：None=无有效钟点。"""
    if not clock or ":" not in clock:
        return None
    try:
        h, m = clock.split(":")
        return now.hour * 60 + now.minute >= int(h) * 60 + int(m)
    except ValueError:
        return None


def next_update(rec: dict, now: datetime | None = None) -> dict:
    """
    计算下次更新：返回 {"daily", "weekdays", "time", "next_text", "next_days"}。
    next_days: -2 已完结；-1 观察中；>=0 实际天数（0=今天）。
    """
    now = now or datetime.now()
    today = now.date()
    daily, weekdays, clock = schedule_pattern(rec)
    finished = rec.get("finished", False)

    passed = _clock_passed(clock, now)
    if finished:
        next_days = -2
    elif daily:
        next_days = 1 if passed is True else 0
    elif not weekdays:
        next_days = -1
    else:
        today_w = today.isoweekday()
        d = today if (today_w in weekdays and passed is not True) else today + timedelta(days=1)
        found = False
        for _ in range(7):
            if d.isoweekday() in weekdays:
                found = True
                break
            d += timedelta(days=1)
        next_days = (d - today).days if found else -1

    if finished:
        next_text = "已完结"
    elif next_days < 0:
        next_text = "观察中"
    elif next_days == 0:
        next_text = f"今日 {clock} 更新" if clock else "今日更新"
    else:
        d = today + timedelta(days=next_days)
        base = f"{WEEK_NAMES[d.isoweekday()]} · {d.month}月{d.day}日"
        next_text = f"{base} {clock}" if clock else base
    return {"daily": daily, "weekdays": weekdays, "time": clock,
            "next_text": next_text, "next_days": next_days}


def estimate_finish(rec: dict, today: date, current: int, total: int, finished: bool) -> str:
    """按历史更新速度推算完结日期，返回「预计X月X日完结」或空串。"""
    if finished or total == 0 or current >= total:
        return ""
    points = [(d, v) for d, v in sorted(rec.get("history", {}).items()) if d != today.isoformat()]
    if len(points) < 2:
        return ""
    first_d = date.fromisoformat(points[0][0])
    days = max((today - first_d).days, 1)
    speed = (current - points[0][1]) / days
    if speed <= 0:
        return ""
    need = int(-(-(total - current) / speed))  # ceil(remain/speed)
    eta = today + timedelta(days=need)
    return f"预计{eta.month}月{eta.day}日完结"


# ---------- 断更检测 ----------

def is_lagging(rec: dict, today: date) -> bool:
    """
    断更判断：未完结、有更新规律，但超过 9 天（>1 个更新周期+缓冲）没有任何集数上涨。
    """
    if rec.get("finished"):
        return False
    daily, weekdays, _ = schedule_pattern(rec)
    if not daily and not weekdays:
        return False
    last_up = None
    prev = None
    for d in sorted(rec.get("history", {})):
        v = rec["history"][d]
        if prev is not None and v > prev:
            last_up = date.fromisoformat(d)
        prev = v
    if last_up is None:
        return False
    return (today - last_up).days > 9


# ---------- 推送文案 ----------

def build_push_report(now: datetime, updated: list[dict], finished: list[dict],
                      added: list[dict]) -> tuple[str, str]:
    """
    构建推送 (标题, 正文)。正文为纯文本（宿主通知渠道会渲染），
    样式贴近效果图微信卡：剧名 + 最新进度 + 更新时刻。
    """
    date_txt = f"{now.month}月{now.day}日"
    segs: list[str] = []
    for v in updated:
        segs.append(f"{v['title']}+{v['delta']}集" if v.get("delta", 0) > 0 else v["title"])
    for v in finished:
        segs.append(f"{v['title']}完结")
    for v in added:
        segs.append(f"新追{v['title']}")
    if segs:
        body = "，".join(segs)
        if len(body) > 26:
            body = body[:25] + "…"
        title = f"{date_txt} {body}"
    else:
        title = f"追剧速递 {date_txt}"

    lines = [f"📺 追剧速递 {date_txt}", ""]
    if updated:
        lines.append("🔥 今日更新")
        for v in updated:
            lines.append(f"· 〔{v['platform_name']}〕{v['title']}：{v['progress']}（+{v['delta']}）")
        lines.append("")
    if finished:
        lines.append("🎉 已完结")
        for v in finished:
            lines.append(f"· 〔{v['platform_name']}〕{v['title']}：{v['progress']}，可以开刷了")
        lines.append("")
    if added:
        lines.append("✨ 新加入追剧清单")
        for v in added:
            lines.append(f"· 〔{v['platform_name']}〕{v['title']}：{v['progress']}")
        lines.append("")
    if not (updated or finished or added):
        lines.append("今日追的剧都没有更新，安心休息～")
        lines.append("")
    lines.append(f"> 检查时间 {now.hour:02d}:{now.minute:02d}，由追剧助手自动发送")
    return title, "\n".join(lines)


def build_weekly_report(now: datetime, days: list[tuple[date, list[dict]]]) -> tuple[str, str]:
    """下周看点：days 为 [(日期, [{title, platform_name, text}])]。"""
    title = "📅 下周追剧看点"
    lines = [f"📅 下周追剧看点（{now.month}月{now.day}日 起）", ""]
    any_item = False
    for d, items in days:
        if not items:
            continue
        any_item = True
        lines.append(f"【{WEEK_NAMES[d.isoweekday()]} · {d.month}月{d.day}日】")
        for it in items:
            lines.append(f"· 〔{it['platform_name']}〕《{it['title']}》{it['text']}")
        lines.append("")
    if not any_item:
        lines.append("未来 7 天暂无排播规律可推算的更新。坚持每天检查，规律攒够 2 个更新日就能推算啦。")
    return title, "\n".join(lines)


def build_discover_report(fresh: list[dict]) -> tuple[str, str]:
    """新剧上线速递：fresh = [{title, rate, url, episodes}]。"""
    title = "🆕 新剧上线速递"
    lines = ["🆕 新剧上线速递", ""]
    for i, h in enumerate(fresh, 1):
        rate = f"豆瓣 {h['rate']}" if h.get("rate") else "暂无评分"
        ep = f" · {h['episodes']}" if h.get("episodes") else ""
        lines.append(f"{i}. 《{h['title']}》 {rate}{ep}")
    lines.append("")
    lines.append("🔍 打开追剧助手「新剧发现」页可一键订阅")
    return title, "\n".join(lines)
