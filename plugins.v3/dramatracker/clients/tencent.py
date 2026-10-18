# 腾讯视频搜索（pbaccess 接口，集数藏在 imgTag 嵌套 JSON 字符串中）

from __future__ import annotations

import json
import uuid

from .. import core


def search_all(client, name: str) -> list[dict]:
    body = {
        "version": "26022601",
        "clientType": 1,
        "filterValue": "",
        "uuid": str(uuid.uuid4()),
        "retry": 0,
        "query": name,
        "pagenum": 0,
        "isPrefetch": False,
        "pagesize": 15,
        "queryFrom": 0,
        "sceneId": 0,
        "isneedQc": 1,
        "preQid": "",
        "adClientInfo": "",
        "extraInfo": {
            "isNewMarkLabel": "0",
            "multi_terminal_pc": "1",
            "themeType": "0",
            "sugRelatedIds": "{}",
            "appVersion": "",
            "frontVersion": "26060108",
        },
        "featureList": ["DEFAULT_FEFEATURE", "PC_WANT_EPISODE_V2", "PC_WANT_EPISODE"],
    }
    resp = client.post(
        "https://pbaccess.video.qq.com/trpc.videosearch.mobile_search."
        "MultiTerminalSearch/MbSearch?vversion_platform=2",
        json=body,
        headers={"Referer": "https://v.qq.com/", "Origin": "https://v.qq.com"},
    )
    resp.raise_for_status()
    items = (((resp.json().get("data") or {}).get("normalList") or {}).get("itemList")) or []

    out: list[dict] = []
    for item in items:
        vi = item.get("videoInfo") or {}
        if vi.get("videoType") != 2:
            continue
        title = vi.get("title", "")

        # imgTag 是 JSON 字符串，取 ["4"]["info"]["text"] 集数徽章
        episode_text = ""
        try:
            tag = json.loads(vi.get("imgTag") or "")
            episode_text = ((tag.get("4") or {}).get("info") or {}).get("text") or ""
        except (ValueError, AttributeError):
            pass

        current, total, finished = core.parse_episode_text(episode_text)
        # 个别条目 imgTag 缺失但有 episodeUpdated
        if current == 0:
            current, _, finished = core.parse_episode_text(vi.get("episodeUpdated") or "")

        cid = ((item.get("doc") or {}).get("id")) or ""
        link = f"https://v.qq.com/x/cover/{cid}.html" if cid else ""

        out.append({
            "title": core.clean_title(title),
            "platform": "tencent",
            "current": current,
            "total": total,
            "finished": bool(finished),
            "link": link,
            "cover": core.https_url(vi.get("imgUrl", "")),
            "schedule": "",
        })
    return out
