# 优酷搜索（search.youku.com/api/search）
# 封面优先 posterDTO.vThumbUrl（竖版海报）；部分剧 sourceImg 是平台通用 logo，仅作兜底

from __future__ import annotations

from .. import core


def search_all(client, name: str) -> list[dict]:
    resp = client.get(
        "https://search.youku.com/api/search",
        params={"pageNo": 1, "pageSize": 10, "keyword": name},
        headers={"Referer": "https://www.youku.com/"},
    )
    resp.raise_for_status()
    components = resp.json().get("pageComponentList") or []

    out: list[dict] = []
    for comp in components:
        common = comp.get("commonData") or {}
        if common.get("episodeType") != 1:
            continue

        total_field = common.get("episodeTotal") or 0
        if not isinstance(total_field, int) or isinstance(total_field, bool):
            total_field = 0

        # stripeBottom 形如 "76集全" / "更新至12集"
        current, total, finished = core.parse_episode_text(common.get("stripeBottom") or "")
        if total == 0:
            total = total_field
        if current == 0:
            current = total_field
        if total > 0 and current >= total:
            finished = True

        show_id = str(common.get("showId") or "")
        action = (((common.get("action") or {}).get("leftButtonDTO") or {}).get("action")) or {}
        value = action.get("value") or ""
        link = value if value.startswith("http") else (
            f"https://v.youku.com/v_show/id_{show_id}.html" if show_id else ""
        )

        cover = ((common.get("posterDTO") or {}).get("vThumbUrl")) or common.get("sourceImg") or ""

        out.append({
            "title": core.clean_title((common.get("titleDTO") or {}).get("displayName", "")),
            "platform": "youku",
            "current": current,
            "total": total,
            "finished": bool(finished),
            "link": link,
            "cover": core.https_url(cover),
            "schedule": "",
        })
    return out
