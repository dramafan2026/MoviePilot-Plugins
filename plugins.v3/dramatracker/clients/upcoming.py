# 待映（即将上映）数据源 —— 豆瓣电影「即将上映」页
# https://movie.douban.com/cinema/later/ 公开无签名，含封面/片名/上映日期。
# 页面结构：div#showing-soon > div.item.mod > a.thumb>img + div.intro>h3>a(片名) + p(日期「11月07日」)
# 注：原方案为猫眼待映页，实测猫眼网页全面反爬（重定向到验证/兜底页），故改用豆瓣。

from __future__ import annotations

import re
from datetime import date, datetime

from .. import core

_ITEM_RE = re.compile(
    r'<div class="item[^"]*">\s*'
    r'<a class="thumb" href="(?P<url>https://movie\.douban\.com/subject/\d+/)">\s*'
    r'<img src="(?P<img>[^"]+)"[^>]*/?>\s*</a>\s*'
    r'<div class="intro">\s*<h3>\s*<a[^>]*>(?P<title>[^<]+)</a>',
    re.S,
)
_DATE_RE = re.compile(r"(\d{1,2})月(\d{1,2})日")


def upcoming(client, limit: int = 12) -> list[dict]:
    """
    拉取即将上映影片，按上映日期升序，返回：
    [{title, date(YYYY-MM-DD), days(距今天数), url, cover, release_text}]
    """
    today = date.today()
    try:
        resp = client.get(
            "https://movie.douban.com/cinema/later/",
            headers={"Referer": "https://movie.douban.com/"},
        )
        resp.raise_for_status()
        html = resp.text
    except Exception:
        return []

    out: list[dict] = []
    seen: set[str] = set()
    for m in _ITEM_RE.finditer(html):
        title = (m.group("title") or "").strip()
        url = m.group("url")
        if not title or url in seen:
            continue
        # 日期在该条目 intro 块内：从当前位置向后取一小段找「X月X日」
        tail = html[m.end(): m.end() + 800]
        dm = _DATE_RE.search(tail)
        if not dm:
            continue
        try:
            d = date(today.year, int(dm.group(1)), int(dm.group(2)))
        except ValueError:
            continue
        if d < today:  # 页面跨年时兜底进位
            try:
                d = date(today.year + 1, int(dm.group(1)), int(dm.group(2)))
            except ValueError:
                continue
        seen.add(url)
        out.append({
            "title": core.clean_title(title),
            "date": d.isoformat(),
            "days": (d - today).days,
            "url": url,
            "cover": core.https_url(m.group("img").strip()),
            "release_text": f"{d.month}月{d.day}日 上映",
        })
        if len(out) >= limit:
            break
    out.sort(key=lambda x: x["days"])
    return out


def today_releases(items: list[dict]) -> list[dict]:
    """过滤出「今天上映」的影片（上映当日提醒推送用）。"""
    t = date.today().isoformat()
    return [it for it in items if (it.get("date") or "")[:10] == t]
