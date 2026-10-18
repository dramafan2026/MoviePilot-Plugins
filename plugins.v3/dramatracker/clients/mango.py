# 芒果TV 搜索与集数
# 搜索接口 mobileso.bz.mgtv.com/pc/search/v2 为签名接口（web 端 JS 逆向）：
# signature = md5(salt + 排序encodeURI参数串 + salt)，salt 逆向自 so.mgtv.com 前端 bundle。
# 正剧条目 type=serial 且 source=imgo（芒果自有）；集数来自 pcweb.api.mgtv.com/episode/list。
# 芒果无完结标记无计划总集数，展示「更新至N集」。

from __future__ import annotations

import hashlib
import time
import uuid

from .. import core

SALT = "xHAa3YZflWLogZUOzl"

# JS encodeURI：保留 ;/?:@&=+$-_.!~*'()# 与字母数字，其余按 UTF-8 百分号编码
_URI_SAFE = set(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
    ";/?:@&=+$-_.!~*'()#"
)


def encode_uri(s: str) -> str:
    return "".join(c if c in _URI_SAFE else "".join(f"%{b:02X}" for b in c.encode("utf-8"))
                   for c in s)


def _now_iso_z() -> str:
    """UTC ISO-8601 秒级（芒果签名 timestamp 格式）。"""
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def search_raw(client, name: str) -> dict:
    """带签名调用 search/v2。"""
    params = {
        "src": "mgtv",
        "did": str(uuid.uuid4()),
        "timestamp": _now_iso_z(),
        "signVersion": "1",
        "signNonce": uuid.uuid4().hex,
        "q": name,
    }
    # 签名串：键字节序排序、空值剔除、值 encodeURI 语义
    raw = "&".join(f"{k}={encode_uri(v)}" for k, v in sorted(params.items()) if v.strip())
    signature = hashlib.md5(f"{SALT}{raw}{SALT}".encode()).hexdigest()

    query = dict(params)
    query["signature"] = signature
    resp = client.get(
        "https://mobileso.bz.mgtv.com/pc/search/v2",
        params=query,
        headers={"Referer": "https://so.mgtv.com/"},
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 200:
        raise RuntimeError(f"芒果搜索返回异常: {data}")
    return data


def search_all(client, name: str) -> list[dict]:
    body = search_raw(client, name)
    contents = ((body.get("data") or {}).get("contents")) or []

    out: list[dict] = []
    for item in contents[:12]:
        if item.get("type") != "serial":
            continue
        data = item.get("data") or {}
        title = data.get("title", "")
        if not title:
            continue
        # 芒果自有源（imgo）：详情链接 + 最新一集 video_id；仅聚合外部平台的条目跳过
        src = next(
            (s for s in (data.get("sourceList") or []) if s.get("source") == "imgo"),
            None,
        )
        if not src:
            continue
        raw_link = src.get("url") or ""
        vid = src.get("vid") or ""
        if not raw_link or not vid:
            continue
        link = f"https:{raw_link}" if raw_link.startswith("//") else raw_link

        out.append({
            "title": core.clean_title(title),
            "platform": "mango",
            "current": latest_episode(client, vid) or 0,
            "total": 0,  # 芒果无「计划总集数」
            "finished": False,
            "link": link,
            "cover": core.https_url(data.get("pic", "")),
            "schedule": "",
        })
    return out


def latest_episode(client, vid: str) -> int:
    """最新正片集号：page1 拿 total_page，翻最后一页取最大 t1。"""
    d = _episode_page(client, vid, 1)
    if not d:
        return 0
    total_page = max(int(d.get("total_page") or 1), 1)
    if total_page > 1:
        d = _episode_page(client, vid, total_page) or d
    max_no = 0
    for ep in d.get("list") or []:
        try:
            n = int(str(ep.get("t1") or "").strip())
        except ValueError:
            continue
        if n > max_no:
            max_no = n
    return max_no


def _episode_page(client, vid: str, page: int) -> dict | None:
    try:
        resp = client.get(
            "https://pcweb.api.mgtv.com/episode/list",
            params={
                "video_id": vid, "size": 35, "page": page,
                "platform": 3, "adjustSmallest": 1, "httpErrorCorrect": 1, "_support": 1080,
            },
            headers={"Referer": "https://www.mgtv.com/"},
        )
        resp.raise_for_status()
        return resp.json().get("data")
    except Exception:
        return None
