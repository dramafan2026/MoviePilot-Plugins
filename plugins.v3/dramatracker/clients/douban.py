# 豆瓣热播榜单（新剧发现数据源）
# 主走官方 App（frodo）接口：HMAC-SHA1 请求签名，数据更全（2:3 海报、评分、更新集数、主演）；
# 豆瓣对高频裸请求有 IP 风控（报伪装的 996「签名错误」），失败自动回退网页版
# search_subjects（数据少但长期稳定），榜单永不落空。结果带 30 分钟内存缓存。
#
# 踩坑记录（勿改错）：
# - 签名 HMAC-SHA1(secret, "GET&{quote(含/api/v2的完整路径, safe='')}&{yyyymmdd}") 再 base64，
#   路径必须百分号编码（/ -> %2F），漏了会一直 400 且极易误判为风控；
# - frodo 必须用纯 frodo UA 的独立 client，不能混入浏览器 UA（双 UA 被按首个判签名错误）。

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from datetime import datetime

from .. import core

FRODO_BASE = "https://frodo.douban.com/api/v2"
FRODO_KEY = "0dad551ec0f84ed02907ff5c42e8ec70"
FRODO_SECRET = "bf7dddc7c9cfe6f7"
FRODO_UA = ("api-client/1 com.douban.frodo/7.22.0.beta9(231) Android/23 product/Mate 40 "
            "vendor/HUAWEI model/Mate 40 brand/HUAWEI  rom/android  network/wifi  "
            "platform/AndroidPad")

# 固定榜单端点；自由标签走 /tv/recommend?tags=
COLLECTION_PATHS = {
    "热门": "/subject_collection/tv_hot/items",
    "国产剧": "/subject_collection/tv_domestic/items",
    "美剧": "/subject_collection/tv_american/items",
    "日剧": "/subject_collection/tv_japanese/items",
    "韩剧": "/subject_collection/tv_korean/items",
    "动画": "/subject_collection/tv_animation/items",
    "综艺": "/subject_collection/tv_variety_show/items",
}

FRODO_CLIENT = None  # 惰性初始化的独立 frodo client（纯 frodo UA）
WEB_CLIENT = None
_CACHE: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL = 1800  # 30 分钟


def _frodo_client():
    global FRODO_CLIENT
    if FRODO_CLIENT is None:
        import httpx
        FRODO_CLIENT = httpx.Client(
            headers={"User-Agent": FRODO_UA},
            timeout=15,
            # 豆瓣 frodo 网关对 HTTP/2 指纹间歇性回 400，强制对齐 App 抓包的 HTTP/1.1
            http2=False,
        )
    return FRODO_CLIENT


def _web_client():
    global WEB_CLIENT
    if WEB_CLIENT is None:
        import httpx
        WEB_CLIENT = httpx.Client(
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                                   "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36"},
            timeout=15,
        )
    return WEB_CLIENT


def _quote_path(path: str) -> str:
    """百分号编码（safe=''，/ 也要编码）——frodo 签名对路径的要求。"""
    safe = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_.~"
    return "".join(c if c in safe else "".join(f"%{b:02X}" for b in c.encode("utf-8"))
                   for c in path)


def frodo_sig(path: str, ts: str) -> str:
    """HMAC-SHA1(secret, "GET&{完整路径含/api/v2}&{yyyymmdd}") 再 base64。"""
    raw = f"GET&{_quote_path(path)}&{ts}"
    digest = hmac.new(FRODO_SECRET.encode(), raw.encode(), hashlib.sha1).digest()
    return base64.b64encode(digest).decode()


def _get_frodo(path: str, extra: dict | None = None) -> dict:
    ts = datetime.now().strftime("%Y%m%d")
    params = {
        "apiKey": FRODO_KEY,
        "os_rom": "android",
        "_ts": ts,
        "_sig": frodo_sig(f"/api/v2{path}", ts),
        "count": "24",
        "start": "0",
    }
    if extra:
        params.update(extra)
    resp = _frodo_client().get(f"{FRODO_BASE}{path}", params=params)
    resp.raise_for_status()
    return resp.json()


def hot_frodo(tag: str) -> list[dict]:
    """frodo 榜单；自由标签走 /tv/recommend（条目在 items[].target，与榜单字段同构）。"""
    if tag in COLLECTION_PATHS:
        path, extra = COLLECTION_PATHS[tag], None
    else:
        path, extra = "/tv/recommend", {"tags": tag, "sort": "U"}
    resp = _get_frodo(path, extra)

    items = resp.get("subject_collection_items")
    if items is None:
        items = resp.get("items")
    out: list[dict] = []
    for it in items or []:
        it = it.get("target") or it  # recommend 条目包一层 target
        title = (it.get("title") or "").strip()
        if not title:
            continue
        rating = (it.get("rating") or {}).get("value")
        rate = ""
        if isinstance(rating, (int, float)):
            rate = str(int(rating)) if float(rating).is_integer() else str(rating)
        card = it.get("card_subtitle") or ""
        parts = [p.strip() for p in card.split(" / ")]
        subtitle = parts[0] if len(parts) <= 1 else f"{parts[0]} · {parts[-1]}"
        sid = str(it.get("id") or "")
        out.append({
            "title": title,
            "rate": rate,
            "url": f"https://movie.douban.com/subject/{sid}/" if sid else "",
            "cover": core.https_url((it.get("pic") or {}).get("large") or ""),
            "episodes": (it.get("episodes_info") or "").strip(),
            "subtitle": subtitle,
        })
    return out


def hot_web(tag: str) -> list[dict]:
    """回退：网页版 search_subjects（只支持预设分类标签）。"""
    resp = _web_client().get(
        "https://movie.douban.com/j/search_subjects",
        params={"type": "tv", "tag": tag, "page_limit": 24, "page_start": 0},
        headers={"Referer": "https://movie.douban.com/tv/"},
    )
    resp.raise_for_status()
    out: list[dict] = []
    for s in (resp.json().get("subjects")) or []:
        title = (s.get("title") or "").strip()
        if not title:
            continue
        out.append({
            "title": title,
            "rate": s.get("rate") or "",
            "url": s.get("url") or "",
            "cover": core.https_url(s.get("cover") or ""),
            "episodes": "",
            "subtitle": "",
        })
    return out


def hot(tag: str) -> list[dict]:
    """拉取热门电视剧榜单（frodo 为主 + 网页版回退 + 30 分钟缓存）。"""
    now = time.time()
    cached = _CACHE.get(tag)
    if cached and now - cached[0] < CACHE_TTL:
        return cached[1]
    try:
        lst = hot_frodo(tag)
        if not lst:
            raise RuntimeError("frodo 空结果")
    except Exception:
        lst = hot_web(tag)
    _CACHE[tag] = (now, lst)
    return lst
