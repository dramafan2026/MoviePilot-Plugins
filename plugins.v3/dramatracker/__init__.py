# 追剧助手 DramaTracker —— MoviePilot V3 插件
#
# 五平台（爱奇艺/腾讯/优酷/B站/芒果TV）追剧订阅与更新检测、新集/完结/断更推送、
# 官方排播时刻展示与追剧日历、豆瓣新剧发现（可选自动追剧规则）、待映倒计时、
# 周报与 ICS 日历订阅。复用桌面版（Rust）已验证的接口与规则，Python 薄壳实现。
#
# 合同遵循 V3 插件指南：
# - 导入期不启动任务/不访问网络；init_plugin 按配置建状态，可重复调用；
#   stop_service 可重复安全释放。
# - 存储/推送全部走宿主内置能力（save_data / post_message），零第三方依赖
#   （httpx 为宿主自带）。

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, List, Optional, Tuple, Type

from apscheduler.triggers.cron import CronTrigger
from fastapi.responses import Response as FastAPIResponse

from app.sdk.events import Event, eventmanager
from app.sdk.logging import logger
from app.sdk.plugin import _PluginBase
from app.schemas.types import EventType

from . import checker, core
from .clients import CLIENT, bilibili, iqiyi, upcoming
from .clients import search_all as client_search_all

PLUGIN_ID = "DramaTracker"

# ICS 日历固定路由名
_ICS_URL_HINT = "api/v1/plugin/DramaTracker/ics"


def _default_config() -> dict:
    return {
        "enabled": False,
        "check_time": "08:30",
        "notify_when_no_update": False,
        "silent_enabled": True,
        "silent_start": "23:00",
        "silent_end": "08:00",
        "notify_new_drama": True,
        "discover_tags": "热门,国产剧",
        "discover_daily_limit": 6,
        "discover_min_rate": 0,
        "discover_block_words": "",
        "auto_subscribe": False,
        "auto_subscribe_max": 2,
        "upcoming_notify": True,
        "weekly_report": True,
        "onlyonce": False,
        "search_query": "",
    }


class DramaTracker(_PluginBase):
    """追剧助手：五平台追剧订阅、更新推送、排播日历与新剧发现。"""

    plugin_name = "追剧助手"
    plugin_desc = "五平台追剧订阅，新集/完结/断更推送，官方排播时刻与追剧日历，豆瓣新剧发现、待映倒计时，微信即可追更。"
    plugin_icon = "DramaTracker.png"
    plugin_version = "1.2.4"
    plugin_author = "dramafan2026"
    author_url = "https://github.com/dramafan2026/MoviePilot-Plugins"
    plugin_config_prefix = "dramatracker_"
    plugin_order = 30
    auth_level = 1

    _config: dict = {}
    _subscriptions: dict = {}

    def init_plugin(self, config: dict | None = None) -> None:
        """读取配置建立运行状态；可重复调用（保存配置/重载均会触发）。"""
        cfg = dict(_default_config())
        cfg.update(config or {})
        self._config = cfg

        # 订阅表：{key: {name, platform, cover}}
        self._subscriptions = self.get_data("subscriptions") or {}
        if self.get_data("state") is None:
            self.save_data("state", {"shows": {}, "hot_seen": {}, "hot_last": "",
                                     "fresh": [], "pending": "", "weekly_last": "",
                                     "upcoming_last": ""})

        # 配置页「搜索剧名」变化 → 后台检索五平台，候选缓存供详情页渲染
        query = str(cfg.get("search_query") or "").strip()
        if query and query != (self.get_data("search_last") or ""):
            self.save_data("search_last", query)
            import threading
            threading.Thread(target=self._cache_search, args=(query,), daemon=True).start()

        # 「立即运行一次」：延迟 3 秒在后台线程执行
        if cfg.get("onlyonce"):
            import threading
            self._onlyonce_timer = threading.Timer(3.0, self.daily_task)
            self._onlyonce_timer.daemon = True
            self._onlyonce_timer.start()
            cfg["onlyonce"] = False
            self.update_config(cfg)
            self._config = cfg

    def get_state(self) -> bool:
        return bool(self._config.get("enabled"))

    # ---------- 配置页 ----------

    def get_form(self) -> Tuple[List[dict], dict]:
        """Vuetify JSON 配置页 + 默认模型。"""
        return [
            {
                "component": "VForm",
                "content": [
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "enabled", "label": "启用插件",
                                              "color": "success"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "check_time", "label": "每日检查时刻",
                                              "placeholder": "08:30", "hint": "每天此时间检查更新并推送"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "onlyonce", "label": "立即运行一次",
                                              "color": "success"},
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 6},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "search_query", "label": "搜索剧名",
                                              "placeholder": "例如：庆余年",
                                              "clearable": True, "persistent-hint": True,
                                              "hint": "保存配置即后台检索五平台，候选在「详情」页一键订阅"},
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VRow",
                        "content": [
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "notify_when_no_update",
                                              "label": "无更新也推送", "color": "success"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 12, "md": 4},
                                "content": [{
                                    "component": "VSwitch",
                                    "props": {"model": "silent_enabled",
                                              "label": "静默时段", "color": "success",
                                              "hint": "静默期内不推送，暂存到时段结束后补发"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 6, "md": 2},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "silent_start", "label": "开始",
                                              "placeholder": "23:00"},
                                }],
                            },
                            {
                                "component": "VCol",
                                "props": {"cols": 6, "md": 2},
                                "content": [{
                                    "component": "VTextField",
                                    "props": {"model": "silent_end", "label": "结束",
                                              "placeholder": "08:00"},
                                }],
                            },
                        ],
                    },
                    {
                        "component": "VExpansionPanels",
                        "props": {"variant": "accordion"},
                        "content": [
                            self._panel("新剧发现（豆瓣热播榜）", [
                                {
                                    "component": "VRow",
                                    "content": [
                                        self._col("VSwitch", "notify_new_drama", "启用新剧上线提醒"),
                                        self._col("VTextField", "discover_tags", "榜单标签",
                                                  "逗号分隔：热门,国产剧,美剧,日剧,韩剧,动画,综艺 或自由标签"),
                                        self._col("VTextField", "discover_daily_limit", "每次最多推送条数", "6"),
                                        self._col("VTextField", "discover_min_rate", "最低评分（0 不限）", "0"),
                                        self._col("VTextField", "discover_block_words", "关键词黑名单",
                                                  "逗号分隔，命中不推送"),
                                        self._col("VSwitch", "auto_subscribe", "自动追剧"),
                                        self._col("VTextField", "auto_subscribe_max", "每日自动订阅上限", "2"),
                                    ],
                                },
                            ]),
                            self._panel("待映与周报", [
                                {
                                    "component": "VRow",
                                    "content": [
                                        self._col("VSwitch", "upcoming_notify", "待映上映当日提醒"),
                                        self._col("VSwitch", "weekly_report", "每周一推送下周看点"),
                                    ],
                                },
                            ]),
                        ],
                    },
                ],
            }
        ], _default_config()

    @staticmethod
    def _panel(title: str, content: list[dict]) -> dict:
        return {
            "component": "VExpansionPanel",
            "content": [
                {"component": "VExpansionPanelTitle", "text": title,
                 "props": {"class": "font-weight-bold"}},
                {"component": "VExpansionPanelText", "content": content},
            ],
        }

    @staticmethod
    def _col(component: str, model: str, label: str, hint: str = "") -> dict:
        props: dict[str, Any] = {"model": model, "label": label}
        if hint:
            props["hint"] = hint
            props["persistent-hint"] = True
        if component == "VSwitch":
            props["color"] = "success"
        comp = {"component": component, "props": props}
        return {
            "component": "VCol",
            "props": {"cols": 12, "md": 6},
            "content": [comp],
        }

    # ---------- 详情页 ----------

    def get_page(self) -> Optional[List[dict]]:
        """插件详情页：今日速览 / 追剧清单 / 日历 / 新剧发现 / 待映倒计时。"""
        state = self.get_data("state") or {}
        recs = state.get("shows") or {}
        subs = self._subscriptions or {}
        today = date.today()

        # 清单视图
        views = []
        for key, sc in subs.items():
            rec = recs.get(key) or {}
            nu = core.next_update(rec) if rec else {}
            views.append({
                "剧名": rec.get("title") or sc.get("name", ""),
                "平台": core.PLATFORM_NAMES.get(sc.get("platform", ""), sc.get("platform", "")),
                "进度": core.progress_text(rec.get("current", 0), rec.get("total", 0), rec.get("finished", False)),
                "排播": rec.get("schedule") or "观察中",
                "下次更新": nu.get("next_text", "—"),
                "链接": rec.get("link", ""),
            })
        calendar_rows = [
            {
                "剧名": v["剧名"], "平台": v["平台"], "排播": v["排播"],
                "下次更新": v["下次更新"], "进度": v["进度"],
            }
            for v in views
        ]

        fresh = state.get("fresh") or []
        try:
            maoyan_items = upcoming.upcoming(CLIENT, limit=8)
        except Exception:
            maoyan_items = []

        elements: List[dict] = []

        # ---- 统计条 ----
        today_cnt = 0
        for key, sc in subs.items():
            rec = recs.get(key) or {}
            if rec and core.next_update(rec).get("next_days") == 0:
                today_cnt += 1

        # ---- 本周分组（schedule_pattern：官方排播文案优先，历史规律兜底）----
        day_names = {1: "周一", 2: "周二", 3: "周三", 4: "周四", 5: "周五", 6: "周六", 7: "周日"}
        daily_names: List[str] = []
        buckets = {i: [] for i in range(1, 8)}
        pending_names: List[str] = []
        for key, sc in subs.items():
            rec = recs.get(key) or {}
            title = rec.get("title") or sc.get("name", "")
            try:
                daily, wds, _clock = core.schedule_pattern(rec)
            except Exception:
                daily, wds = False, []
            if daily:
                daily_names.append(title)
            elif wds:
                for w in wds:
                    buckets[w].append(title)
            else:
                pending_names.append(title)

        def _stat(num: int, label: str) -> dict:
            return {"component": "VCol", "props": {"cols": 6, "sm": 3}, "content": [{
                "component": "VCard",
                "props": {"variant": "tonal", "class": "text-center py-3"},
                "content": [
                    {"component": "VCardText", "text": str(num),
                     "props": {"class": "text-h5 font-weight-bold"}},
                    {"component": "VCardText", "text": label,
                     "props": {"class": "text-caption text-medium-emphasis"}},
                ],
            }]}

        elements.append({
            "component": "VRow", "props": {"class": "mb-1"}, "content": [
                _stat(len(subs), "在追作品"),
                _stat(today_cnt, "今日更新"),
                _stat(sum(len(v) for v in buckets.values()) + len(daily_names), "本周有档"),
                _stat(len(maoyan_items), "待映影片"),
            ]})
        elements.append({
            "component": "VRow", "props": {"class": "mb-1"}, "content": [
                {"component": "VCol", "props": {"cols": 6, "sm": 3}, "content": [{
                    "component": "VBtn", "text": "⚡ 立即检查",
                    "props": {"color": "primary", "variant": "flat", "block": True},
                    "events": {"click": {"api": f"plugin/{PLUGIN_ID}/check",
                                         "method": "post", "params": {}}},
                }]},
            ]})

        # 搜索订阅卡：候选由配置页「搜索剧名」检索后缓存（详情页输入框无法回传后端）
        elements.append(self._search_card())

        # ---- 五个板块的内容构造 ----
        week_rows = []
        for label, names in ([("每日档", daily_names)]
                             + [(day_names[i], buckets[i]) for i in range(1, 8)]
                             + [("待观察", pending_names)]):
            week_rows.append({"星期": label, "剧目": "、".join(names) if names else "—"})

        def _dt(items: list, headers: list) -> dict:
            return {"component": "VDataTable",
                    "props": {"items": items, "headers": headers,
                              "density": "compact", "hide-default-footer": True}}

        list_page: list = [_dt(views, [
            {"title": "剧名", "key": "剧名"}, {"title": "平台", "key": "平台"},
            {"title": "进度", "key": "进度"}, {"title": "排播", "key": "排播"},
            {"title": "下次更新", "key": "下次更新"}])] if views else [{
                "component": "VAlert", "props": {"type": "info", "variant": "tonal",
                                                 "text": "还没有订阅。到「配置」页填写「搜索剧名」并保存，或在「新剧发现」点「追」。"}}]

        week_page = [{"component": "VCard", "props": {"variant": "tonal",
                      "subtitle": "按官方排播优先、历史规律兜底归档"},
                      "content": [_dt(week_rows, [
                          {"title": "星期", "key": "星期"},
                          {"title": "剧目", "key": "剧目"}])]}]

        cal_page: list = [_dt(calendar_rows, [
            {"title": "剧名", "key": "剧名"}, {"title": "平台", "key": "平台"},
            {"title": "排播", "key": "排播"}, {"title": "下次更新", "key": "下次更新"},
            {"title": "进度", "key": "进度"}])] if calendar_rows else [{
                "component": "VAlert", "props": {"type": "info", "variant": "tonal",
                                                 "text": "暂无日历数据。"}}]

        fresh_page: list
        if fresh:
            cards = []
            for f in fresh:
                cards.append({
                    "component": "VCol",
                    "props": {"cols": 6, "md": 2},
                    "content": [{
                        "component": "VCard",
                        "props": {"variant": "tonal", "subtitle": f.get("rate") or "暂无评分"},
                        "content": [
                            {"component": "VImg",
                             "props": {"src": self._cover_proxy(f.get("cover", "")),
                                       "aspect-ratio": "2/3", "cover": True}},
                            {"component": "VCardText",
                             "text": f"{f.get('title', '')} {f.get('episodes', '')}",
                             "props": {"class": "text-caption"}},
                            {"component": "VBtn", "text": "＋ 追",
                             "props": {"size": "x-small", "color": "primary",
                                       "variant": "tonal", "block": True},
                             "events": {"click": {
                                 "api": f"plugin/{PLUGIN_ID}/autosub",
                                 "method": "post",
                                 "params": {"name": f.get("title", "")}}}},
                        ],
                    }],
                })
            fresh_page = [{"component": "VCard", "props": {"variant": "tonal",
                          "subtitle": "近 7 天豆瓣热播新剧，点「追」直接订阅"},
                          "content": [{"component": "VRow", "content": cards}]}]
        else:
            fresh_page = [{"component": "VAlert", "props": {"type": "info", "variant": "tonal",
                           "text": "暂无新剧发现（每日自动检查豆瓣热播榜单）。"}}]

        soon_page: list
        if maoyan_items:
            rows = [{"片名": it["title"], "上映": it["release_text"],
                     "倒计时": f"还有 {it['days']} 天"} for it in maoyan_items]
            soon_page = [{"component": "VCard", "props": {"variant": "tonal",
                         "subtitle": "豆瓣即将上映，上映当日提醒"},
                         "content": [_dt(rows, [
                             {"title": "片名", "key": "片名"},
                             {"title": "上映", "key": "上映"},
                             {"title": "倒计时", "key": "倒计时"}])]}]
        else:
            soon_page = [{"component": "VAlert", "props": {"type": "info", "variant": "tonal",
                          "text": "暂无即将上映影片数据。"}}]

        # ---- 五个板块（手风琴：PageRender 无 model 机制，VTabs+VWindow
        # 无法联动切换内容区，改用 VExpansionPanels 点击展开，100% 可用）----
        sections = [
            ("📺 追剧清单", list_page),
            ("🗓 本周速览", week_page),
            ("📅 影视日历", cal_page),
            ("🆕 新剧发现", fresh_page),
            ("🎬 待映倒计时", soon_page),
        ]
        elements.append({
            "component": "VExpansionPanels",
            "props": {"variant": "accordion", "class": "mt-2"},
            "content": [
                {
                    "component": "VExpansionPanel",
                    "content": [
                        {"component": "VExpansionPanelTitle", "text": t,
                         "props": {"class": "font-weight-bold"}},
                        {"component": "VExpansionPanelText", "content": c},
                    ],
                }
                for t, c in sections
            ],
        })
        return elements

    def _cover_proxy(self, url: str) -> str:
        """豆瓣封面有防盗链，前端经本插件图片代理端点加载。"""
        if not url:
            return ""
        if "doubanio.com" in url:
            return f"/api/v1/plugin/{PLUGIN_ID}/cover?url={url}"
        return url

    def _cache_search(self, query: str) -> None:
        """后台检索五平台并把候选写入插件存储，供详情页渲染。"""
        try:
            res = self.api_search(query)
            self.save_data("search_results", {
                "query": query, "items": (res.get("items") or [])[:12],
                "errors": res.get("errors") or [],
            })
        except Exception as e:
            logger.warning(f"追剧助手搜索失败：{e}")
            self.save_data("search_results", {"query": query, "items": [],
                                              "errors": [f"搜索失败：{e}"]})

    def _search_card(self, with_buttons: bool = True) -> dict:
        """搜索订阅卡：展示配置页关键词的检索候选，点「订阅」经原生事件调入后端。

        工作台（DashboardRender）不支持 events，故 with_buttons=False 时只展示候选。
        """
        sr = self.get_data("search_results") or {}
        items = sr.get("items") or []
        query = str(sr.get("query") or "")
        errors = [e for e in (sr.get("errors") or []) if e]
        if not items:
            content: list[dict] = [{
                "component": "VAlert", "props": {
                    "type": "info", "variant": "tonal", "density": "compact",
                    "text": "在「配置」页填写「搜索剧名」并保存，本页即列出五平台候选；"
                            "也可发送命令 /dt_search 剧名。"}}]
            for err in errors:
                content.append({"component": "VAlert", "props": {
                    "type": "warning", "variant": "tonal", "density": "compact",
                    "text": err}})
            return {"component": "VCard",
                    "props": {"variant": "tonal", "title": "🔍 搜索订阅"},
                    "content": content}
        if not with_buttons:
            return {"component": "VCard",
                    "props": {"variant": "tonal",
                              "title": f"🔍 搜索结果 · {query}" if items else "🔍 搜索订阅",
                              "subtitle": "在插件详情页点「订阅」"},
                    "content": [{"component": "VList", "props": {"density": "compact"},
                                 "content": [{
                                     "component": "VListItem", "props": {
                                         "title": it.get("title", ""),
                                         "subtitle": f"{core.PLATFORM_NAMES.get(it.get('platform', ''), '')} · "
                                                     f"{core.progress_text(it.get('current', 0), it.get('total', 0), it.get('finished', False))}"}}
                                     for it in items] or [{
                                     "component": "VListItem",
                                     "props": {"title": "尚未搜索",
                                               "subtitle": "到「配置」页填写「搜索剧名」并保存"}}]}]}
        rows = []
        for it in items:
            rows.append({
                "component": "VCol", "props": {"cols": 6, "md": 3}, "content": [{
                    "component": "VCard", "props": {"variant": "tonal"},
                    "content": [
                        {"component": "VImg", "props": {
                            "src": self._cover_proxy(it.get("cover", "")),
                            "aspect-ratio": "2/3", "cover": True}},
                        {"component": "VCardText",
                         "text": f"{it.get('title', '')}"
                                 f"〔{core.PLATFORM_NAMES.get(it.get('platform', ''), '')}〕"
                                 f" {core.progress_text(it.get('current', 0), it.get('total', 0), it.get('finished', False))}",
                         "props": {"class": "text-caption"}},
                        {"component": "VBtn", "text": "＋ 订阅",
                         "props": {"size": "small", "color": "primary",
                                   "variant": "flat", "block": True},
                         "events": {"click": {
                             "api": f"plugin/{PLUGIN_ID}/subscribe", "method": "post",
                             "params": {"name": it.get("title", ""),
                                        "platform": it.get("platform", ""),
                                        "cover": it.get("cover", "")}}}},
                    ]}]})
        return {"component": "VCard",
                "props": {"variant": "tonal", "title": f"🔍 搜索结果 · {query}",
                          "subtitle": "点「订阅」即开启追更"},
                "content": [{"component": "VRow", "props": {"class": "pt-2"},
                             "content": rows}]}

    # ---------- 动态 API ----------

    def get_api(self) -> List[dict]:
        return [
            {"path": "/search", "endpoint": self.api_search, "methods": ["GET", "POST"],
             "auth": "bear", "summary": "五平台聚合搜索候选"},
            {"path": "/subscribe", "endpoint": self.api_subscribe, "methods": ["GET", "POST"],
             "auth": "bear", "summary": "订阅剧集"},
            {"path": "/quick_add", "endpoint": self.api_subscribe, "methods": ["GET"],
             "auth": "apikey", "summary": "订阅剧集（iOS 快捷指令/外部系统）"},
            {"path": "/autosub", "endpoint": self.api_autosub, "methods": ["POST"],
             "auth": "bear", "summary": "按剧名自动搜索并订阅最佳候选"},
            {"path": "/unsubscribe", "endpoint": self.api_unsubscribe, "methods": ["GET", "POST"],
             "auth": "bear", "summary": "取消订阅"},
            {"path": "/list", "endpoint": self.api_list, "methods": ["GET"],
             "auth": "bear", "summary": "追剧清单"},
            {"path": "/calendar", "endpoint": self.api_calendar, "methods": ["GET"],
             "auth": "bear", "summary": "追剧日历"},
            {"path": "/discover", "endpoint": self.api_discover, "methods": ["GET"],
             "auth": "bear", "summary": "豆瓣新剧发现"},
            {"path": "/upcoming", "endpoint": self.api_upcoming, "methods": ["GET"],
             "auth": "bear", "summary": "待映倒计时"},
            {"path": "/today", "endpoint": self.api_today, "methods": ["GET"],
             "auth": "bear", "summary": "今日更新视图"},
            {"path": "/check", "endpoint": self.api_check, "methods": ["POST"],
             "auth": "bear", "summary": "手动触发检查"},
            {"path": "/ics", "endpoint": self.api_ics, "methods": ["GET"],
             "auth": "apikey", "summary": "追剧日历 ICS 订阅"},
            {"path": "/cover", "endpoint": self.api_cover, "methods": ["GET"],
             "auth": "bear", "summary": "豆瓣封面图代理"},
        ]

    def api_search(self, name: str = "", platform: str = "") -> dict:
        """五平台聚合搜索；失败平台原因随 errors 透传。"""
        if not name.strip():
            return {"success": False, "message": "请输入剧名"}
        p = core.parse_platform(platform) if platform else None
        try:
            items, errors = client_search_all(p, name.strip())
        except Exception as e:
            return {"success": False, "message": str(e), "items": [], "errors": []}
        return {"success": True, "items": items, "errors": errors}

    def api_subscribe(self, name: str = "", platform: str = "iqiyi", cover: str = "") -> dict:
        """订阅剧集（同时供 iOS 快捷指令 quick_add 使用）。"""
        if not name.strip():
            return {"success": False, "message": "缺少剧名"}
        p = core.parse_platform(platform)
        if not p:
            return {"success": False, "message": f"不支持的平台：{platform}"}
        key = core.show_key(p, name)
        self._subscriptions[key] = {"name": name.strip(), "platform": p, "cover": cover}
        self.save_data("subscriptions", self._subscriptions)
        # 建立基线需网络拉详情（数秒到数十秒），移到后台线程执行，
        # 订阅本身已落库，立即返回避免前端同步请求超时（「服务器返回了无效响应」）
        import threading
        threading.Thread(target=self._check_one_now, args=(p, name.strip()), daemon=True).start()
        return {"success": True, "message": f"已订阅《{name.strip()}》，正在获取最新集数", "key": key}

    def api_autosub(self, name: str = "") -> dict:
        """按剧名五平台聚合搜索并订阅最佳候选（平台自动识别）。

        五平台搜索 + 建立基线可达数十秒，同步执行会令前端请求超时
        （表现为「服务器返回了无效响应」），故改为后台线程执行并立即返回，
        结果经消息通知。
        """
        name = str(name or "").strip()
        if not name:
            return {"success": False, "message": "缺少剧名"}
        import threading
        threading.Thread(target=self._autosub_bg, args=(name,), daemon=True).start()
        return {"success": True,
                "message": f"正在搜索并订阅《{name}》，完成后将发送消息通知"}

    def _autosub_bg(self, name: str) -> None:
        """后台：五平台搜索 → 订阅最佳候选 → 消息通知结果。"""
        try:
            items, errors = client_search_all(None, name)
            if not items:
                msg = f"没有搜到《{name}》的正剧" + ("；" + "；".join(errors) if errors else "")
                self.post_message(title="追剧助手 · 订阅失败", text=msg)
                return
            top = items[0]
            result = self.api_subscribe(top["title"], top.get("platform", ""),
                                        top.get("cover", ""))
            if not result.get("success"):
                self.post_message(title="追剧助手 · 订阅失败",
                                  text=result.get("message", "订阅失败"))
        except Exception as e:
            logger.warning(f"DramaTracker 自动订阅《{name}》异常: {e}")
            try:
                self.post_message(title="追剧助手 · 订阅失败",
                                  text=f"自动订阅《{name}》失败：{e}")
            except Exception:
                pass

    def api_unsubscribe(self, key: str = "") -> dict:
        if key in self._subscriptions:
            self._subscriptions.pop(key, None)
            self.save_data("subscriptions", self._subscriptions)
            state = self.get_data("state") or {}
            recs = state.get("shows") or {}
            recs.pop(key, None)
            self.save_data("state", state)
            return {"success": True, "message": "已取消订阅"}
        return {"success": False, "message": "订阅不存在"}

    def api_list(self) -> dict:
        state = self.get_data("state") or {}
        recs = state.get("shows") or {}
        out = []
        for key, sc in (self._subscriptions or {}).items():
            rec = recs.get(key) or {}
            out.append({
                "key": key,
                "name": sc.get("name"),
                "platform": sc.get("platform"),
                "platform_name": core.PLATFORM_NAMES.get(sc.get("platform", ""), ""),
                "cover": self._cover_proxy(rec.get("cover") or sc.get("cover") or ""),
                "current": rec.get("current", 0),
                "total": rec.get("total", 0),
                "finished": rec.get("finished", False),
                "progress": core.progress_text(rec.get("current", 0), rec.get("total", 0), rec.get("finished", False)),
                "schedule": rec.get("schedule", ""),
                "next": core.next_update(rec).get("next_text", "观察中") if rec else "—",
                "link": rec.get("link", ""),
                "error": rec.get("error", ""),
            })
        return {"success": True, "items": out}

    def api_calendar(self) -> dict:
        state = self.get_data("state") or {}
        recs = state.get("shows") or {}
        out = []
        for key, sc in (self._subscriptions or {}).items():
            rec = recs.get(key) or {}
            if not rec:
                continue
            nu = core.next_update(rec)
            out.append({
                "key": key,
                "title": rec.get("title") or sc.get("name"),
                "platform": sc.get("platform"),
                "platform_name": core.PLATFORM_NAMES.get(sc.get("platform", ""), ""),
                "cover": self._cover_proxy(rec.get("cover") or sc.get("cover") or ""),
                "current": rec.get("current", 0),
                "total": rec.get("total", 0),
                "finished": rec.get("finished", False),
                "progress": core.progress_text(rec.get("current", 0), rec.get("total", 0), rec.get("finished", False)),
                "daily": nu.get("daily"),
                "weekdays": nu.get("weekdays"),
                "time": nu.get("time", ""),
                "next_text": nu.get("next_text", ""),
                "next_days": nu.get("next_days", -1),
                "link": rec.get("link", ""),
            })
        out.sort(key=lambda x: (x["next_days"] < 0, x["next_days"]))
        return {"success": True, "items": out}

    def api_discover(self, tag: str = "") -> dict:
        """豆瓣榜单（30 分钟缓存）；已订阅的剧带 subscribed 标记。"""
        tags = [tag.strip()] if tag.strip() else [t.strip() for t in str(self._config.get("discover_tags") or "热门").split(",") if t.strip()]
        state = self.get_data("state") or {}
        result = []
        for t in tags[:3]:
            try:
                lst = self._hot_fn()(t)
            except Exception as e:
                result.append({"tag": t, "items": [], "error": str(e)[:80]})
                continue
            wl = [core.clean_title(s.get("name", "")) for s in self._subscriptions.values()]
            for h in lst:
                dt = core.clean_title(h["title"])
                h["subscribed"] = bool(dt) and any((st in dt) or (dt in st) for st in wl)
                h["cover"] = self._cover_proxy(h.get("cover", ""))
            result.append({"tag": t, "items": lst, "error": ""})
        return {"success": True, "tags": result}

    def api_upcoming(self) -> dict:
        items = upcoming.upcoming(CLIENT, limit=12)
        return {"success": True, "items": items}

    def api_today(self) -> dict:
        """今日更新视图（含断更提示）。"""
        state = self.get_data("state") or {}
        recs = state.get("shows") or {}
        out = []
        for key, sc in (self._subscriptions or {}).items():
            rec = recs.get(key) or {}
            if not rec:
                continue
            nu = core.next_update(rec)
            out.append({
                "key": key,
                "title": rec.get("title") or sc.get("name"),
                "platform_name": core.PLATFORM_NAMES.get(sc.get("platform", ""), ""),
                "cover": self._cover_proxy(rec.get("cover") or sc.get("cover") or ""),
                "progress": core.progress_text(rec.get("current", 0), rec.get("total", 0), rec.get("finished", False)),
                "schedule": rec.get("schedule", ""),
                "next_text": nu.get("next_text", ""),
                "next_days": nu.get("next_days", -1),
                "lagging": core.is_lagging(rec, date.today()),
                "finished": rec.get("finished", False),
                "link": rec.get("link", ""),
            })
        return {"success": True, "items": out, "date_text": f"{date.today().month}月{date.today().day}日"}

    def api_check(self) -> dict:
        """手动触发一次检查（走静默暂存逻辑）。

        全量检查（订阅更新 + 新剧发现 + 待映 + 周报）耗时较长，
        同步执行会令前端请求超时，改为后台线程执行并立即返回，
        检查结果与推送经消息通知。
        """
        import threading
        threading.Thread(target=self._check_bg, daemon=True).start()
        return {"success": True, "message": "检查已启动，完成后将发送消息通知"}

    def _check_bg(self) -> None:
        try:
            self.daily_task()
        except Exception as e:
            logger.warning(f"DramaTracker 手动检查异常: {e}")
            try:
                self.post_message(title="追剧助手 · 检查失败", text=str(e))
            except Exception:
                pass

    def api_ics(self) -> FastAPIResponse:
        """未来 30 天追剧日历 ICS（原生响应 text/calendar）。"""
        state = self.get_data("state") or {}
        recs = state.get("shows") or {}
        today = date.today()
        stamp = today.strftime("%Y%m%d")
        ics = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//dramatracker//CN",
               "CALSCALE:GREGORIAN", "X-WR-CALNAME:追剧助手"]
        n = 0
        for i in range(30):
            d = today + timedelta(days=i)
            w = d.isoweekday()
            for key, sc in (self._subscriptions or {}).items():
                rec = recs.get(key) or {}
                if not rec or rec.get("finished"):
                    continue
                daily, weekdays, clock = core.schedule_pattern(rec)
                if not daily and w not in weekdays:
                    continue
                # 当天若已检查到真实更新（历史含今天）则仍保留事件
                n += 1
                title = rec.get("title") or sc.get("name", "")
                cur = rec.get("current", 0)
                text = f"更新至{cur + 1}集" if not daily else "更新"
                summary = self._ics_escape(f"《{title}》{text}（{core.PLATFORM_NAMES.get(sc.get('platform', ''), '')}）{clock}")
                ics += ["BEGIN:VEVENT", f"UID:{d.strftime('%Y%m%d')}-{n}@dramatracker",
                        f"DTSTAMP:{stamp}T000000Z",
                        f"DTSTART;VALUE=DATE:{d.strftime('%Y%m%d')}",
                        f"SUMMARY:{summary}"]
                if rec.get("link"):
                    ics.append(f"URL:{self._ics_escape(rec['link'])}")
                ics.append("END:VEVENT")
        ics.append("END:VCALENDAR")
        return FastAPIResponse(content="\r\n".join(ics), media_type="text/calendar")

    @staticmethod
    def _ics_escape(s: str) -> str:
        return (s or "").replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")

    def api_cover(self, url: str = "") -> FastAPIResponse:
        """豆瓣封面代理：服务端带 Referer 拉图，规避防盗链破图。"""
        if not url.startswith("http") or "doubanio.com" not in url:
            return FastAPIResponse(content="bad url", status_code=400)
        try:
            r = CLIENT.get(url, headers={"Referer": "https://movie.douban.com/"})
            r.raise_for_status()
            media = r.headers.get("content-type", "image/jpeg").split(";")[0]
            return FastAPIResponse(content=r.content, media_type=media)
        except Exception as e:
            logger.warning(f"追剧助手封面代理失败：{e}")
            return FastAPIResponse(content="fetch failed", status_code=502)

    # ---------- 定时服务 ----------

    def get_service(self) -> List[dict]:
        if not self.get_state():
            return []
        hh, mm = "8", "30"
        try:
            hh, mm = str(self._config.get("check_time") or "08:30").split(":")
        except ValueError:
            pass
        return [{
            "id": f"{PLUGIN_ID}.Check",
            "name": "追剧助手每日检查",
            "trigger": CronTrigger(hour=int(hh), minute=int(mm), second=0),
            "func": self.daily_task,
            "kwargs": {},
        }]

    def daily_task(self) -> str:
        """每日主任务：冲刷暂存 → 检查推送 → 周报 → 新剧发现 → 待映提醒。"""
        cfg = self._config
        now = datetime.now()
        state = self.get_data("state") or {"shows": {}}
        messages: list[str] = []

        # 1) 冲刷静默期暂存
        pending = state.get("pending") or ""
        if pending and not checker.in_silent_window(now, cfg.get("silent_start", "23:00"), cfg.get("silent_end", "08:00")):
            self._post(pending.split("|", 1)[0], pending.split("|", 1)[1])
            messages.append("暂存推送已补发")
            state["pending"] = ""

        # 2) 检查全部订阅
        shows = list((self._subscriptions or {}).values())
        if shows:
            report, state = checker.run_check(
                shows, state, self._search_fn(), self._strategy_fn(), now)
            self.save_data("state", state)

            if report["has_news"] or cfg.get("notify_when_no_update"):
                title, text = core.build_push_report(now, report["updated"], report["finished"], report["added"])
                if checker.in_silent_window(now, cfg.get("silent_start", "23:00"), cfg.get("silent_end", "08:00")):
                    state["pending"] = f"{title}|{text}"
                    self.save_data("state", state)
                    messages.append("处于静默时段，已暂存")
                else:
                    self._post(title, text)
                    messages.append("推送已发送" if report["has_news"] else "无更新（按设置推送）")

            # 3) 周报：周一推送未来 7 天看点
            if cfg.get("weekly_report") and now.weekday() == 0 and state.get("weekly_last") != today_text(now):
                try:
                    title, text = self._build_weekly(now)
                    self._post(title, text)
                    state["weekly_last"] = today_text(now)
                    self.save_data("state", state)
                    messages.append("下周看点已发送")
                except Exception as e:
                    logger.warning(f"追剧助手周报失败：{e}")
        else:
            messages.append("暂无订阅")

        # 4) 新剧发现（每日一次）
        if cfg.get("notify_new_drama") and state.get("hot_last") != today_text(now):
            try:
                fresh, state = checker.run_discover(
                    state, [s.get("name", "") for s in (self._subscriptions or {}).values()],
                    self._hot_fn(),
                    [t.strip() for t in str(cfg.get("discover_tags") or "热门").split(",") if t.strip()],
                    cfg, now)
                if fresh:
                    state["fresh"] = (state.get("fresh") or []) + [
                        {"title": f["title"], "rate": f.get("rate", ""), "url": f.get("url", ""),
                         "cover": f.get("cover", ""), "episodes": f.get("episodes", ""),
                         "seen": today_text(now)} for f in fresh]
                    state["fresh"] = state["fresh"][-12:]
                    title, text = core.build_discover_report(fresh)
                    self._post(title, text)
                    messages.append(f"新剧提醒{len(fresh)}条")
                    # 自动追剧规则：按榜单顺序自动订阅
                    if cfg.get("auto_subscribe"):
                        self._auto_subscribe(fresh)
                self.save_data("state", state)
            except Exception as e:
                logger.warning(f"追剧助手新剧发现失败：{e}")

        # 5) 待映上映当日提醒（每天一次）
        if cfg.get("upcoming_notify") and state.get("upcoming_last") != today_text(now):
            try:
                items = upcoming.today_releases(upcoming.upcoming(CLIENT, limit=24))
                state["upcoming_last"] = today_text(now)
                self.save_data("state", state)
                if items:
                    lines = ["🎬 今日上映", ""]
                    for it in items[:6]:
                        lines.append(f"· 《{it['title']}》 {it['release_text']}")
                    self._post("🎬 今日上映提醒", "\n".join(lines))
                    messages.append(f"待映提醒{len(items)}条")
            except Exception as e:
                logger.warning(f"追剧助手待映提醒失败：{e}")

        result = "；".join(messages) or "检查完成"
        logger.info(f"追剧助手每日任务：{result}")
        return result

    def _post(self, title: str, text: str) -> None:
        """宿主通知中心推送（配置渠道同步送达）。"""
        try:
            self.post_message(title=title, text=text)
        except Exception as e:
            logger.error(f"追剧助手推送失败：{e}")

    def _search_fn(self):
        def _f(platform: str, name: str):
            if platform == "bilibili":
                return bilibili.search_picked(CLIENT, name)
            return client_search_all(platform, name)
        return _f

    def _strategy_fn(self):
        def _f(_platform: str, info: dict):
            qipu = iqiyi.qipu_from_link(info.get("link", ""))
            return iqiyi.fetch_update_strategy(CLIENT, qipu) if qipu else ""
        return _f

    def _hot_fn(self):
        from .clients import douban
        return douban.hot

    def _build_weekly(self, now: datetime) -> tuple[str, str]:
        """未来 7 天看点：按天分组（真实记录 + 排播规律推算）。"""
        state = self.get_data("state") or {"shows": {}}
        recs = state.get("shows") or {}
        days = []
        for i in range(7):
            d = now.date() + timedelta(days=i)
            w = d.isoweekday()
            items = []
            for key, sc in (self._subscriptions or {}).items():
                rec = recs.get(key) or {}
                if not rec or rec.get("finished"):
                    continue
                daily, weekdays, clock = core.schedule_pattern(rec)
                if not daily and w not in weekdays:
                    continue
                base = "今日更新" if i == 0 else "预计更新"
                items.append({
                    "title": rec.get("title") or sc.get("name", ""),
                    "platform_name": core.PLATFORM_NAMES.get(sc.get("platform", ""), ""),
                    "text": f"{base} {clock}" if clock else base,
                })
            days.append((d, items))
        return core.build_weekly_report(now, days)

    def _auto_subscribe(self, fresh: list[dict]) -> None:
        """自动追剧：对发现的新剧逐个搜索订阅，每日上限 auto_subscribe_max。"""
        from datetime import date as _d
        state = self.get_data("state") or {}
        today = _d.today().isoformat()
        if state.get("auto_sub_date") != today:
            state["auto_sub_count"] = 0
            state["auto_sub_date"] = today
        limit = int(self._config.get("auto_subscribe_max") or 2)
        subscribed = 0
        for h in fresh:
            if state.get("auto_sub_count", 0) + subscribed >= limit:
                break
            # 依次尝试各平台，搜到即订阅
            for p in ("iqiyi", "tencent", "youku", "bilibili", "mango"):
                try:
                    info = self._search_fn()(p, h["title"])
                except Exception:
                    continue
                if info:
                    key = core.show_key(p, h["title"])
                    if key not in self._subscriptions:
                        self._subscriptions[key] = {"name": h["title"], "platform": p, "cover": info.get("cover", "")}
                        self._check_one_now(p, h["title"])
                        subscribed += 1
                    break
        if subscribed:
            self.save_data("subscriptions", self._subscriptions)
            state["auto_sub_count"] = state.get("auto_sub_count", 0) + subscribed
            self.save_data("state", state)

    def _check_one_now(self, platform: str, name: str) -> None:
        """订阅后立即拉一次建立基线（失败不影响订阅）。"""
        try:
            state = self.get_data("state") or {"shows": {}}
            _, state = checker.run_check(
                [{"name": name, "platform": platform, "cover": ""}], state,
                self._search_fn(), self._strategy_fn())
            self.save_data("state", state)
        except Exception as e:
            logger.warning(f"追剧助手基线检查失败：{e}")

    # ---------- 远程命令 ----------

    @staticmethod
    def get_command() -> List[dict]:
        return [
            {"cmd": "/dt_today", "event": EventType.PluginAction,
             "desc": "追剧助手：今天有什么更新", "category": "追剧助手",
             "data": {"action": "dt_today"}},
            {"cmd": "/dt_check", "event": EventType.PluginAction,
             "desc": "追剧助手：立即检查并推送", "category": "追剧助手",
             "data": {"action": "dt_check"}},
            {"cmd": "/dt_search", "event": EventType.PluginAction,
             "desc": "追剧助手：搜索剧集（/dt_search 庆余年）", "category": "追剧助手",
             "data": {"action": "dt_search"}},
        ]

    @eventmanager.register(EventType.PluginAction)
    def handle_command(self, event: Event) -> None:
        """远程命令路由：只处理属于本插件的 action。"""
        data = event.event_data or {}
        action = data.get("action")
        if action not in ("dt_today", "dt_check", "dt_search"):
            return
        try:
            if action == "dt_today":
                title, text = self._today_text()
                self._post(title, text)
            elif action == "dt_check":
                result = self.daily_task()
                self._post("追剧助手", f"检查完成：{result}")
            else:  # dt_search
                query = str(data.get("query") or data.get("args") or "").strip()
                cmd_text = str(data.get("cmd") or "").strip()
                if not query and cmd_text:
                    parts = cmd_text.split(None, 1)
                    query = parts[1].strip() if len(parts) > 1 else ""
                if not query:
                    self._post("追剧助手", "用法：/dt_search 剧名")
                    return
                self._search_and_reply(query)
        except Exception as e:
            logger.error(f"追剧助手命令执行失败：{e}")
            self._post("追剧助手", f"命令执行失败：{e}")

    def _today_text(self) -> tuple[str, str]:
        """今天有什么更新：今日档期 + 进度一览。"""
        state = self.get_data("state") or {"shows": {}}
        recs = state.get("shows") or {}
        now = datetime.now()
        today = now.date()
        lines = [f"📺 今日追剧速览 {today.month}月{today.day}日", ""]
        hits = 0
        for key, sc in (self._subscriptions or {}).items():
            rec = recs.get(key) or {}
            if not rec:
                continue
            nu = core.next_update(rec)
            name = rec.get("title") or sc.get("name", "")
            prog = core.progress_text(rec.get("current", 0), rec.get("total", 0), rec.get("finished", False))
            if nu.get("next_days") == 0:
                lines.append(f"🔥 {name}：今日更新 {nu.get('time', '')}（{prog}）".replace("  ", " "))
                hits += 1
            elif nu.get("next_text"):
                lines.append(f"· {name}：{nu['next_text']}（{prog}）")
        if hits == 0 and len(lines) <= 2:
            lines.append("今日暂无更新的剧集，安心休息～")
        return "追剧助手·今日更新", "\n".join(lines)

    def _search_and_reply(self, query: str) -> None:
        """远程搜索并回复候选（可回复序号订阅由消息交互实现，此处直接订阅最佳候选）。"""
        items, errors = client_search_all(None, query)
        if not items:
            msg = f"《{query}》没有搜到可订阅的正剧。" + ("；".join(errors) if errors else "")
            self._post("追剧助手", msg)
            return
        top = items[0]
        self.api_subscribe(top["title"], top["platform"], top.get("cover", ""))
        lines = [f"已订阅《{top['title']}》〔{core.PLATFORM_NAMES.get(top['platform'], top['platform'])}〕",
                 f"当前：{core.progress_text(top.get('current', 0), top.get('total', 0), top.get('finished', False))}", ""]
        if len(items) > 1:
            lines.append("其他候选（打开插件页面可订阅）：")
            for it in items[1:6]:
                lines.append(f"· 〔{core.PLATFORM_NAMES.get(it['platform'], '')}〕{it['title']}")
        self._post("追剧助手", "\n".join(lines))

    # ---------- 仪表板 ----------

    def get_dashboard_meta(self) -> Optional[List[dict]]:
        """仪表盘元信息：key 在当前插件内唯一。"""
        if not self.get_state():
            return None
        return [
            {"key": "Today", "name": "追剧助手 · 今日更新"},
            {"key": "Search", "name": "追剧助手 · 搜索订阅"},
            {"key": "Upcoming", "name": "追剧助手 · 待映倒计时"},
        ]

    def get_dashboard(self, key: str = "", **kwargs) -> Optional[Tuple[dict, dict, List[dict]]]:
        """仪表盘页面：(列配置, 全局配置, 页面元素 JSON)。"""
        if not self.get_state():
            return None
        today_rows, upcoming_rows = self._dashboard_data()
        today_el = {
            "component": "VCard",
            "props": {"variant": "tonal", "title": "📺 今日更新",
                      "subtitle": f"共 {len(today_rows)} 部今日档期"},
            "content": [{
                "component": "VList", "props": {"density": "compact"},
                "content": [
                    {"component": "VListItem",
                     "props": {"title": r["title"],
                               "subtitle": f"{r['platform_name']} · {r['next_text']}"}}
                    for r in today_rows
                ] or [{"component": "VListItem",
                       "props": {"title": "今日暂无更新", "subtitle": "安心休息～"}}],
            }],
        }
        soon_el = {
            "component": "VCard",
            "props": {"variant": "tonal", "title": "🎬 待映倒计时",
                      "subtitle": "豆瓣即将上映"},
            "content": [{
                "component": "VList", "props": {"density": "compact"},
                "content": [
                    {"component": "VListItem",
                     "props": {"title": r["title"],
                               "subtitle": f"{r['release_text']} · 还有 {r['days']} 天"}}
                    for r in upcoming_rows
                ] or [{"component": "VListItem",
                       "props": {"title": "暂无即将上映数据"}}],
            }],
        }
        search_el = self._search_card(with_buttons=False)
        if key == "Today":
            elements = [today_el]
        elif key == "Search":
            elements = [search_el]
        elif key == "Upcoming":
            elements = [soon_el]
        else:  # 兼容缺省 key 的旧调用
            elements = [today_el, search_el, soon_el]
        return {"cols": 12, "md": 6}, {"refresh": 300, "border": True, "title": "追剧助手"}, elements

    def _dashboard_data(self) -> tuple[list[dict], list[dict]]:
        state = self.get_data("state") or {"shows": {}}
        recs = state.get("shows") or {}
        today_rows = []
        for key, sc in (self._subscriptions or {}).items():
            rec = recs.get(key) or {}
            if not rec:
                continue
            nu = core.next_update(rec)
            if nu.get("next_days") == 0:
                today_rows.append({
                    "title": rec.get("title") or sc.get("name", ""),
                    "platform_name": core.PLATFORM_NAMES.get(sc.get("platform", ""), ""),
                    "next_text": nu.get("next_text", ""),
                })
        try:
            upcoming_rows = upcoming.upcoming(CLIENT, limit=5)
        except Exception:
            upcoming_rows = []
        return today_rows, upcoming_rows

    # ---------- 工作流动作 ----------

    def get_actions(self) -> List[dict]:
        return [{
            "id": "drama_daily_check",
            "name": "追剧检查推送",
            "desc": "执行一次追剧检查并按配置推送更新",
            "func": self.workflow_check,
            "kwargs": {},
        }]

    def workflow_check(self, content: Any = None, **kwargs) -> tuple[bool, Any]:
        """工作流动作实现：返回 (执行状态, ActionContent)。"""
        try:
            self.daily_task()
            return True, content
        except Exception as e:
            logger.error(f"追剧助手工作流动作失败：{e}")
            return False, content

    # ---------- Agent 工具 ----------

    def get_agent_tools(self) -> List[Type]:
        """注册智能体工具「今天有什么更新」；宿主缺 Agent 模块时返回空。"""
        try:
            from app.agent.tools.base import MoviePilotTool
        except Exception:
            return []
        from pydantic import BaseModel, Field

        class DramaTodayInput(BaseModel):
            """工具输入参数模型"""
            explanation: str = Field(..., description="工具使用说明")

        class DramaTodayTool(MoviePilotTool):
            """查询今天追剧清单的更新情况"""
            name: str = "drama_today_updates"
            description: str = ("查询用户追剧清单今天/近期的剧集更新情况，包括今日更新剧集、"
                                "下次更新时间与追剧进度。当用户询问「今天有什么更新」"
                                "「我追的剧更到哪了」时使用此工具。")
            args_schema: Type[BaseModel] = DramaTodayInput

            def get_tool_message(self, **kwargs) -> Optional[str]:
                return "正在查询追剧清单的今日更新"

            async def run(self, **kwargs) -> str:
                plugin = self._get_plugin()
                if not plugin:
                    return "未找到追剧助手插件实例"
                try:
                    title, text = plugin._today_text()
                    return text
                except Exception as e:
                    return f"查询失败：{e}"

            def _get_plugin(self):
                try:
                    from app.sdk.plugins import PluginManager
                    return PluginManager().get_plugin(PLUGIN_ID)
                except Exception:
                    return None

        return [DramaTodayTool]

    # ---------- 生命周期 ----------

    def stop_service(self) -> None:
        """释放后台资源：取消一次性任务线程；定时任务由宿主调度器托管。"""
        t = getattr(self, "_onlyonce_timer", None)
        if t:
            t.cancel()
            self._onlyonce_timer = None
        self._config["enabled"] = False


def today_text(now: datetime) -> str:
    return now.date().isoformat()
