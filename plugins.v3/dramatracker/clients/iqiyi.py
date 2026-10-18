# 爱奇艺搜索（search.video.iqiyi.com/o 接口）
# 另含官方排播文案抓取：pcw-api.iqiyi.com/album/album/baseinfo/{qipu_id} -> data.updateStrategy

from __future__ import annotations

import re

from .. import core


def _first_u64(v: dict, *keys):
    """按顺序取第一个可用数值（数字或纯数字字符串）。"""
    for k in keys:
        n = v.get(k)
        if isinstance(n, bool):
            continue
        if isinstance(n, (int, float)):
            return int(n)
        if isinstance(n, str) and n.strip().isdigit():
            return int(n.strip())
    return 0


def search_all(client, name: str) -> list[dict]:
    resp = client.get(
        "https://search.video.iqiyi.com/o",
        params={"if": "html5", "pageNum": 1, "pageSize": 10, "key": name},
        headers={"Referer": "https://www.iqiyi.com/"},
    )
    resp.raise_for_status()
    docs = (resp.json().get("data") or {}).get("docinfos") or []

    out: list[dict] = []
    for doc in docs:
        album = doc.get("albumDocInfo") or {}
        channel = album.get("channel") or ""
        if not channel.startswith("电视剧"):
            continue

        meta = album.get("video_lib_meta") or {}
        total = _first_u64(album, "itemTotalNumber") or _first_u64(meta, "total_video_count")
        if not total:
            # filmtv_update_strategy 纯数字时为集数
            s = str(meta.get("filmtv_update_strategy") or "").strip()
            total = int(s) if s.isdigit() else 0

        current = _first_u64(album, "newest_item_number")
        finished = (meta.get("current_period") == -1) or (total > 0 and current >= total)

        qipu = meta.get("qipu_id")
        link = f"https://www.iqiyi.com/a_{qipu}.html" if qipu else ""

        out.append({
            "title": core.clean_title(album.get("albumTitle", "")),
            "platform": "iqiyi",
            "current": current,
            "total": total,
            "finished": bool(finished),
            "link": link,
            "cover": core.https_url(album.get("albumImg", "")),
            "schedule": "",
        })
    return out


def qipu_from_link(link: str) -> str | None:
    """从爱奇艺专辑链接 https://www.iqiyi.com/a_xxx.html 提取 qipu_id。"""
    m = re.search(r"/a_([A-Za-z0-9]+)", link or "")
    return m.group(1) if m else None


def fetch_update_strategy(client, qipu_id: str) -> str:
    """拉专辑官方排播文案（如「VIP会员每日18:00更新」）；失败返回空串不影响主流程。"""
    try:
        resp = client.get(
            f"https://pcw-api.iqiyi.com/album/album/baseinfo/{qipu_id}",
            headers={"Referer": "https://www.iqiyi.com/"},
        )
        resp.raise_for_status()
        s = ((resp.json().get("data") or {}).get("updateStrategy") or "").strip()
        return s
    except Exception:
        return ""
