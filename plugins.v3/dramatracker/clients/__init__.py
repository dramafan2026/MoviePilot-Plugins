# 五平台搜索聚合入口 + 豆瓣即将上映。
# 各平台实现均为 search_all(client, name) -> list[dict]，
# 返回 ShowInfo 字典：{title, platform, current, total, finished, link, cover, schedule}。

from __future__ import annotations

import httpx

from . import bilibili, douban, iqiyi, mango, tencent, upcoming, youku

CLIENT = httpx.Client(
    headers={
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
    },
    timeout=15,
    follow_redirects=True,
)

_MODULES = {
    "iqiyi": iqiyi,
    "tencent": tencent,
    "youku": youku,
    "bilibili": bilibili,
    "mango": mango,
}


def search_one(platform: str, name: str) -> list[dict]:
    """单平台全部候选；失败抛异常由上层翻译。"""
    return _MODULES[platform].search_all(CLIENT, name)


def search_all(platform: str | None, name: str) -> tuple[list[dict], list[str]]:
    """
    候选搜索：platform=None 时五平台合并；单平台失败不影响其余，
    失败原因随 errors 返回给前端展示（风控/超时不再静默）。
    """
    from .. import core

    platforms = list(_MODULES) if platform is None else [platform]
    merged: list[dict] = []
    errors: list[str] = []
    for p in platforms:
        try:
            merged.extend(_MODULES[p].search_all(CLIENT, name))
        except Exception as e:
            errors.append(f"{core.PLATFORM_NAMES.get(p, p)}：{friendly_err(e)}")
    merged = [c for c in merged if core.keep_candidate(c, name)]
    merged.sort(key=lambda c: core.title_match_rank(c.get("title", ""), name))
    return merged[:18], errors


def search(platform: str, name: str) -> dict | None:
    """单平台挑最佳候选（订阅检查用）。"""
    from .. import core

    return core.pick(name, _MODULES[platform].search_all(CLIENT, name))


def friendly_err(e: Exception) -> str:
    """把底层网络错误翻译成用户能看懂的一句话。"""
    s = str(e)
    if "403" in s or "forbidden" in s.lower():
        return "搜索被风控，请稍后再试"
    if "timed out" in s or "timeout" in s.lower():
        return "网络超时"
    if "connection" in s.lower() or "sending request" in s:
        return "网络连接失败"
    return s[:60]
