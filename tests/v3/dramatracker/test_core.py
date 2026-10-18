# 追剧助手 · 纯逻辑单测（不依赖宿主环境）
# plugins.v3 目录名含点号，此处用命名空间方式挂载包链。

import importlib.util
import sys
import types
from datetime import date, datetime
from pathlib import Path

PLUGIN_DIR = Path(__file__).resolve().parents[3] / "plugins.v3" / "dramatracker"

if "dtpkg" not in sys.modules:
    root = types.ModuleType("dtpkg")
    root.__path__ = [str(PLUGIN_DIR)]
    sys.modules["dtpkg"] = root
    for _name, _rel in [("core", r"core.py"), ("checker", r"checker.py")]:
        _spec = importlib.util.spec_from_file_location(f"dtpkg.{_name}", str(PLUGIN_DIR / _rel))
        _mod = importlib.util.module_from_spec(_spec)
        sys.modules[f"dtpkg.{_name}"] = _mod
        _spec.loader.exec_module(_mod)

from dtpkg import checker, core  # noqa: E402


# ---------- 平台/标题 ----------

def test_parse_platform():
    assert core.parse_platform("爱奇艺") == "iqiyi"
    assert core.parse_platform("芒果TV") == "mango"
    assert core.parse_platform("bilibili") == "bilibili"
    assert core.parse_platform("不存在") is None


def test_clean_title():
    assert core.clean_title("<em>庆余年</em> 第二季") == "庆余年 第二季"
    assert core.clean_title("某剧电视剧") == "某剧"
    assert core.clean_title("某剧（中国大陆）") == "某剧"


def test_show_key():
    assert core.show_key("iqiyi", "<em>剧名</em>电视剧") == "iqiyi::剧名"


# ---------- 集数解析 ----------

def test_parse_episode_text():
    assert core.parse_episode_text("全36集") == (36, 36, True)
    assert core.parse_episode_text("76集全(完结)") == (76, 76, True)
    assert core.parse_episode_text("更新至52集") == (52, 0, False)
    # 年份数字不能误当集数
    assert core.parse_episode_text("2024年更新至12集")[0] == 12
    assert core.parse_episode_text("2024") == (0, 0, False)
    assert core.parse_episode_text("") == (0, 0, False)


# ---------- 排播文案 ----------

def test_parse_schedule():
    s = core.parse_schedule("VIP会员每日18:00更新")
    assert s["daily"] and s["time"] == "18:00"
    s = core.parse_schedule("VIP会员每日19:30更新3集，非会员每日21:30转免1集")
    assert s["daily"] and s["time"] == "19:30"  # 多档取第一个（VIP 档）
    s = core.parse_schedule("每周三20:00更新2集")
    assert not s["daily"] and s["weekdays"] == [3] and s["time"] == "20:00"
    s = core.parse_schedule("每周一、周二20点更新")
    assert s["weekdays"] == [1, 2] and s["time"] == "20:00"
    assert core.parse_schedule("VIP会员每日更新")["daily"]
    assert core.parse_schedule("") is None
    assert core.parse_schedule("已完结") is None


def test_first_clock():
    assert core._first_clock("每日19点30分更新") == "19:30"
    assert core._first_clock("每日20点更新") == "20:00"
    assert core._first_clock("无钟点") == ""


# ---------- 候选过滤 ----------

def test_is_extra_title():
    assert core.is_extra_title("庆余年 第1集")
    assert core.is_extra_title("庆余年 花絮：名场面")
    assert core.is_extra_title("庆余年 解说 全集速看")
    assert not core.is_extra_title("庆余年 第二季")


def test_keep_candidate():
    ok = {"title": "庆余年", "current": 10, "finished": False}
    assert core.keep_candidate(ok)
    no_ep = {"title": "庆余年", "current": 0, "finished": False}
    assert not core.keep_candidate(no_ep)
    extra = {"title": "庆余年 花絮", "current": 30, "finished": False}
    assert not core.keep_candidate(extra)


def test_pick():
    # 真实链路中 pick 作用于 keep_candidate 过滤后的候选（解说/花絮已剔除）
    cands = [
        {"title": "全然庆余年", "current": 5},
        {"title": "庆余年 第二季", "current": 10},
    ]
    assert core.pick("庆余年", cands)["title"] == "庆余年 第二季"  # 前缀匹配优于包含
    assert core.pick("庆余年", [{"title": "庆余年", "current": 1}])["title"] == "庆余年"
    assert core.pick("不存在", []) is None


# ---------- 排播规律/下次更新 ----------

def _rec(**kw):
    base = {
        "title": "测试剧", "platform": "iqiyi", "current": 10, "total": 0,
        "finished": False, "schedule": "每周三18:00更新",
        "history": {}, "link": "", "cover": "",
    }
    base.update(kw)
    return base


def test_schedule_pattern_official_first():
    daily, weekdays, clock = core.schedule_pattern(_rec())
    assert (not daily) and weekdays == [3] and clock == "18:00"


def test_schedule_pattern_history_fallback():
    # 两个不同星期出现上涨才采信
    rec = _rec(schedule="")
    rec["history"] = {
        "2026-10-05": 8,  # 周一
        "2026-10-07": 10,  # 周三
        "2026-10-12": 12,  # 周一
    }
    daily, weekdays, clock = core.schedule_pattern(rec)
    assert (not daily) and set(weekdays) == {1, 3} and clock == ""
    # 只有一个星期 -> 不采信
    rec2 = _rec(schedule="")
    rec2["history"] = {"2026-10-05": 8, "2026-10-12": 10}
    assert core.schedule_pattern(rec2) == (False, [], "")


def test_next_update():
    now = datetime(2026, 10, 14, 12, 0)  # 周三 12:00
    nu = core.next_update(_rec(), now)
    assert nu["next_days"] == 0 and "18:00" in nu["next_text"]
    # 已过钟点 -> 顺延到下周三
    nu = core.next_update(_rec(), datetime(2026, 10, 14, 19, 0))
    assert nu["next_days"] == 7
    # 完结
    nu = core.next_update(_rec(finished=True), now)
    assert nu["next_days"] == -2


# ---------- 断更/完结预测 ----------

def test_is_lagging():
    today = date(2026, 10, 18)
    rec = _rec(history={"2026-09-28": 8, "2026-10-05": 10})  # 距今 13 天无上涨
    assert core.is_lagging(rec, today)
    rec2 = _rec(history={"2026-10-12": 8, "2026-10-16": 10})
    assert not core.is_lagging(rec2, today)
    assert not core.is_lagging(_rec(finished=True), today)


def test_estimate_finish():
    today = date(2026, 10, 18)
    rec = _rec(history={"2026-10-04": 10, "2026-10-10": 15})
    eta = core.estimate_finish(rec, today, current=20, total=40, finished=False)
    assert eta.startswith("预计")
    assert core.estimate_finish(_rec(), today, 10, 0, False) == ""


# ---------- 文案 ----------

def test_build_push_report():
    now = datetime(2026, 10, 18, 8, 30)
    updated = [{"title": "测试剧", "platform_name": "爱奇艺", "progress": "更新至12/24集", "delta": 2}]
    title, text = core.build_push_report(now, updated, [], [])
    assert "测试剧" in title and "📺" in text and "爱奇艺" in text
    t2, x2 = core.build_push_report(now, [], [], [])
    assert "追剧速递" in t2


def test_build_discover_report():
    title, text = core.build_discover_report([
        {"title": "新剧A", "rate": "8.5", "episodes": "12集"},
        {"title": "新剧B", "rate": "", "episodes": ""},
    ])
    assert "新剧上线" in title and "新剧A" in text and "豆瓣 8.5" in text and "暂无评分" in text


# ---------- 发现/检查流程（mock 网络） ----------

def test_run_discover():
    state = {"hot_seen": {"旧剧": "2026-10-01"}}
    hot = lambda tag: [  # noqa: E731
        {"title": "旧剧", "rate": "9.0"},
        {"title": "新剧A", "rate": "8.5"},
        {"title": "低分剧", "rate": "5.0"},
        {"title": "黑名单词剧", "rate": "9.0"},
    ]
    cfg = {"discover_min_rate": 8.0, "discover_block_words": "黑名单", "discover_daily_limit": 6}
    fresh, st = checker.run_discover(state, [], hot, ["热门"], cfg, datetime(2026, 10, 18, 8, 0))
    assert [f["title"] for f in fresh] == ["新剧A"]
    assert "新剧A" in st["hot_seen"]


def test_run_discover_first_run_no_push():
    state = {}
    hot = lambda tag: [{"title": "新剧A", "rate": "8.5"}]  # noqa: E731
    fresh, _ = checker.run_discover(state, [], hot, ["热门"], {}, datetime(2026, 10, 18, 8, 0))
    assert fresh == []  # 首启仅记录不推送


def test_run_discover_watchlist_skip():
    state = {"hot_seen": {"旧": "2026-10-01"}}
    hot = lambda tag: [{"title": "庆余年", "rate": "9.0"}]  # noqa: E731
    fresh, _ = checker.run_discover(state, ["庆余年 第二季"], hot, ["热门"], {}, datetime(2026, 10, 18, 8, 0))
    assert fresh == []


def test_run_check():
    shows = [{"name": "测试剧", "platform": "iqiyi", "cover": ""}]
    state = {"shows": {}}
    info = {"title": "测试剧", "platform": "iqiyi", "current": 12, "total": 24,
            "finished": False, "link": "", "cover": "http://x/y.jpg", "schedule": "每日18:00更新"}

    # 第一次：建立基线
    rep, state = checker.run_check(shows, state, lambda p, n: dict(info))
    assert rep["added"] and rep["added"][0]["is_new"]
    key = "iqiyi::测试剧"
    assert state["shows"][key]["current"] == 12

    # 第二次：涨 2 集
    info2 = dict(info, current=14)
    rep, state = checker.run_check(shows, state, lambda p, n: info2)
    assert rep["updated"] and rep["updated"][0]["delta"] == 2
    assert rep["has_news"]

    # 第三次：完结
    info3 = dict(info, current=24, finished=True)
    rep, state = checker.run_check(shows, state, lambda p, n: info3)
    assert rep["finished"] and rep["finished"][0]["just_finished"]


def test_run_check_search_fail_keeps_old():
    shows = [{"name": "测试剧", "platform": "iqiyi", "cover": ""}]
    state = {"shows": {"iqiyi::测试剧": {"title": "测试剧", "platform": "iqiyi", "current": 10,
                                        "total": 0, "finished": False, "history": {},
                                        "first_seen": "2026-10-01"}}}
    rep, st = checker.run_check(shows, state, lambda p, n: None)
    assert st["shows"]["iqiyi::测试剧"]["current"] == 10
    assert rep["views"][0]["error"]


def test_in_silent_window():
    assert checker.in_silent_window(datetime(2026, 10, 18, 23, 30), "23:00", "08:00")
    assert checker.in_silent_window(datetime(2026, 10, 18, 2, 0), "23:00", "08:00")
    assert not checker.in_silent_window(datetime(2026, 10, 18, 9, 0), "23:00", "08:00")
    assert not checker.in_silent_window(datetime(2026, 10, 18, 12, 0), "12:00", "12:00")
