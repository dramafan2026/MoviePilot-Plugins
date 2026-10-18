# 检查/发现流程层：可 mock 的纯函数设计。
# run_check / run_discover 不直接依赖宿主与网络，由调用方注入
# search_fn / hot_fn / strategy_fn，便于单测与工作流复用。

from __future__ import annotations

from datetime import date, datetime

from . import core


# ---------- 每日检查 ----------

def run_check(shows: list[dict], state: dict, search_fn, strategy_fn=None,
              now: datetime | None = None) -> tuple[dict, dict]:
    """
    逐个检查订阅剧集，返回 (报告, 新state)。

    shows: 订阅列表 [{name, platform, cover}]
    state: 持久化状态，含 {"shows": {key: 记录}}
    search_fn(platform, name) -> info 字典 | None
    strategy_fn(platform, info) -> 官方排播文案（爱奇艺 updateStrategy 补抓），可空
    报告: {updated, finished, added, views, has_news, date_text}
    """
    now = now or datetime.now()
    today = now.date()
    recs: dict = state.setdefault("shows", {})
    updated, finished, added, views = [], [], [], []

    for sc in shows:
        name = sc.get("name", "").strip()
        platform = sc.get("platform", "")
        if not name or not platform:
            continue
        key = core.show_key(platform, name)
        old = recs.get(key)

        info = None
        try:
            info = search_fn(platform, name)
        except Exception:
            info = None

        if not info:
            # 搜索失败：保留旧记录并标记错误
            views.append(_view(key, sc, old, today, error="没搜到，可能剧名不准或平台无此剧"))
            continue

        delta = (info.get("current", 0) - old["current"]) if old else 0
        just_finished = bool(old and not old.get("finished") and info.get("finished"))
        is_new = old is None

        # 官方排播文案优先级：已落库 > 本次搜索携带 > 爱奇艺补抓（每天至多一次额外请求）
        schedule = ""
        if old and old.get("schedule"):
            schedule = old["schedule"]
        elif info.get("schedule"):
            schedule = info["schedule"]
        elif strategy_fn and platform == "iqiyi" and not info.get("finished") and info.get("link"):
            schedule = strategy_fn(platform, info) or ""

        rec = dict(old) if old else {
            "title": info.get("title", name),
            "platform": platform,
            "first_seen": today.isoformat(),
            "history": {},
        }
        rec.update({
            "title": info.get("title") or name,
            "current": info.get("current", 0),
            "total": info.get("total", 0),
            "finished": bool(info.get("finished")),
            "link": info.get("link", ""),
            "schedule": schedule or (old or {}).get("schedule", ""),
            # 封面：跟随配置/搜索结果持久化，已添加剧集无需重新添加
            "cover": info.get("cover") or sc.get("cover") or (old or {}).get("cover", ""),
            "last_check": today.isoformat(),
            "error": "",
        })
        rec.setdefault("history", {})[today.isoformat()] = info.get("current", 0)
        _prune_history(rec)
        recs[key] = rec

        view = _view(key, sc, rec, today, info=info, delta=delta, is_new=is_new)
        view["just_finished"] = just_finished
        if just_finished:
            finished.append(view)
        if delta > 0 and not just_finished:
            updated.append(view)
        if is_new:
            added.append(view)
        views.append(view)

    report = {
        "updated": updated,
        "finished": finished,
        "added": added,
        "views": views,
        "has_news": bool(updated or finished or added),
        "date_text": f"{today.month}月{today.day}日",
    }
    return report, state


def _prune_history(rec: dict, keep_days: int = 90) -> None:
    """历史集数只保留近 90 天，防止无限膨胀。"""
    cutoff = (date.today().toordinal() - keep_days)
    h = rec.get("history", {})
    rec["history"] = {
        d: v for d, v in h.items()
        if _to_ordinal_safe(d) >= cutoff
    }


def _to_ordinal_safe(d: str) -> int:
    try:
        return date.fromisoformat(d).toordinal()
    except ValueError:
        return 0


def _view(key: str, sc: dict, rec: dict | None, today: date,
          info: dict | None = None, delta: int = 0, is_new: bool = False,
          error: str = "") -> dict:
    rec = rec or {}
    current = info.get("current", 0) if info else rec.get("current", 0)
    total = info.get("total", 0) if info else rec.get("total", 0)
    fin = bool(info.get("finished")) if info else rec.get("finished", False)
    platform = sc.get("platform", rec.get("platform", ""))
    return {
        "key": key,
        "title": (info or rec).get("title") or sc.get("name", ""),
        "platform": platform,
        "platform_name": core.PLATFORM_NAMES.get(platform, platform),
        "current": current,
        "total": total,
        "finished": fin,
        "progress": core.progress_text(current, total, fin),
        "eta": core.estimate_finish(rec, today, current, total, fin) if rec else "",
        "link": (info or rec).get("link", ""),
        "cover": rec.get("cover") or (info or {}).get("cover", ""),
        "delta": delta,
        "just_finished": False,
        "is_new": is_new,
        "error": error,
        "last_check": rec.get("last_check", today.isoformat()),
        "schedule": rec.get("schedule", ""),
        "lagging": core.is_lagging(rec, today) if rec and not error else False,
    }


# ---------- 豆瓣新剧发现 ----------

def run_discover(state: dict, watchlist_names: list[str], hot_fn,
                 tags: list[str], cfg: dict, now: datetime | None = None) -> tuple[list[dict], dict]:
    """
    对比豆瓣热播榜发现新剧，返回 (fresh, 新state)。

    规则：
    - 已在追剧清单的剧集不推（双向模糊匹配，容忍季数写法差异）
    - 首次启用仅记录榜单不推送，避免刷屏
    - 每日最多一次（hot_last），每次最多 discover_daily_limit 条
    - 评分阈值 discover_min_rate / 关键词黑名单 discover_block_words
    """
    now = now or datetime.now()
    today = now.date()
    hot_seen: dict = state.setdefault("hot_seen", {})
    fresh: list[dict] = []
    first_run = not hot_seen

    wl = [core.clean_title(n) for n in watchlist_names if core.clean_title(n)]

    for tag in tags:
        try:
            lst = hot_fn(tag)
        except Exception:
            continue
        for h in lst:
            title = (h.get("title") or "").strip()
            if not title:
                continue
            dt = core.clean_title(title)
            if not dt:
                continue
            if title in hot_seen:
                continue
            hot_seen[title] = today.isoformat()
            if first_run:
                continue
            # 已在追剧清单：不推
            if any((st in dt) or (dt in st) for st in wl):
                continue
            # 评分阈值
            rate = h.get("rate") or ""
            try:
                min_rate = float(cfg.get("discover_min_rate") or 0)
            except (TypeError, ValueError):
                min_rate = 0.0
            if min_rate > 0 and (not rate or float(rate) < min_rate):
                continue
            # 关键词黑名单
            blocks = [w.strip() for w in str(cfg.get("discover_block_words") or "").split(",") if w.strip()]
            if any(b in title for b in blocks):
                continue
            fresh.append(h)

    # hot_seen 只保留近 90 天
    cutoff = today.toordinal() - 90
    state["hot_seen"] = {
        t: d for t, d in hot_seen.items() if _to_ordinal_safe(d) >= cutoff
    }
    state["hot_last"] = today.isoformat()
    return fresh[: int(cfg.get("discover_daily_limit") or 6)], state


# ---------- 静默时段 ----------

def _hhmm(s: str) -> int | None:
    try:
        h, m = str(s).split(":")
        return int(h) * 60 + int(m)
    except (ValueError, AttributeError):
        return None


def in_silent_window(now: datetime, start: str, end: str) -> bool:
    """当前是否处于静默时段（支持跨午夜，如 23:00-08:00）。"""
    s, e, cur = _hhmm(start), _hhmm(end), now.hour * 60 + now.minute
    if s is None or e is None or s == e:
        return False
    if s < e:
        return s <= cur < e
    return cur >= s or cur < e
