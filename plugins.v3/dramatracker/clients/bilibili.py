# B站（番剧/国创）搜索与详情
# 搜索 api.bilibili.com/x/web-interface/search/type?search_type=media_bangumi 无需签名，
# 仅需携带 buvid3 cookie；详情 pgc/view/web/season 公开。
# 注意：publish.weekday 实测全为 0 不可用，用首播日期 pub_time 的星期兜底
#（周更番首播日≈更新日）；剧集数以详情 episodes 纯数字 title 计数为准。

from __future__ import annotations

from datetime import datetime

from .. import core


def search_all(client, name: str) -> list[dict]:
    resp = client.get(
        "https://api.bilibili.com/x/web-interface/search/type",
        params={"search_type": "media_bangumi", "page": 1, "keyword": name},
        headers={
            "Referer": "https://www.bilibili.com/",
            "Cookie": "buvid3=DEADBEEF-4C55-4F3B-9D5E-0123456789ABinfoc",
        },
    )
    resp.raise_for_status()
    data = resp.json()
    if data.get("code") != 0:
        raise RuntimeError(f"B站搜索返回异常: {data.get('message', '')}")

    out: list[dict] = []
    for item in (data.get("data") or {}).get("result") or []:
        if len(out) >= 10:
            break
        title = core.clean_title(item.get("title", ""))
        if not title:
            continue
        # index_show 形如「更新至12话」「全28话」——统一按「集」解析
        index_show = (item.get("index_show") or "").replace("话", "集")
        current, total, finished = core.parse_episode_text(index_show)

        season_id = str(item.get("season_id") or 0)
        if season_id == "0":
            continue
        if total > 0 and current == 0:
            current = total
        if total > 0 and current >= total:
            finished = True

        out.append({
            "title": title,
            "platform": "bilibili",
            "current": current,
            "total": total,
            "finished": bool(finished),
            "link": f"https://www.bilibili.com/bangumi/play/ss{season_id}",
            "cover": core.https_url(item.get("cover", "")),
            "schedule": "",
            "season_id": season_id,
        })
    return out


def search_picked(client, name: str) -> dict | None:
    """搜索 + 对最终候选调详情补官方数据（更新星期/完结/当前话数）。"""
    picked = core.pick(name, search_all(client, name))
    if not picked:
        return None
    detail = season_detail(client, picked.pop("season_id", ""))
    if detail:
        apply_detail(picked, detail)
    return picked


def apply_detail(info: dict, d: dict) -> None:
    """详情补全：完结标记/总话数/正片数/官方更新星期 -> schedule 文案。"""
    publish = d.get("publish") or {}
    if publish.get("is_finish") == 1:
        info["finished"] = True
    total = int(d.get("total") or 0)
    if total > 0:
        info["total"] = total
    # 正片数：episodes 里 title 为纯数字的话数（PV/花絮标题非纯数字）
    eps = d.get("episodes") or []
    n = sum(1 for e in eps if str(e.get("title") or "").isdigit())
    if n > info.get("current", 0):
        info["current"] = n
    if info.get("finished") and info.get("total", 0) > 0:
        info["current"] = info["total"]

    weekday = publish.get("weekday") or 0
    if 1 <= weekday <= 7:
        info["schedule"] = f"每周{'一二三四五六日'[weekday - 1]}"
    else:
        # weekday 普遍为 0：用首播日期的星期兜底
        pub_time = publish.get("pub_time") or ""
        try:
            d0 = datetime.strptime(pub_time[:10], "%Y-%m-%d")
            info["schedule"] = f"每周{'一二三四五六日'[d0.isoweekday() - 1]}"
        except ValueError:
            pass


def season_detail(client, season_id: str) -> dict | None:
    """拉 season 详情；失败返回 None（不影响主流程）。"""
    if not season_id:
        return None
    try:
        resp = client.get(
            "https://api.bilibili.com/pgc/view/web/season",
            params={"season_id": season_id},
            headers={"Referer": "https://www.bilibili.com/"},
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") != 0:
            return None
        return data.get("result")
    except Exception:
        return None
