# 追剧助手 DramaTracker

> MoviePilot V3 追剧插件 —— 五平台追剧订阅、更新推送、排播日历、豆瓣新剧发现，微信里即可追更。

![MoviePilot V3](https://img.shields.io/badge/MoviePilot-V3-3ddc97) ![Version](https://img.shields.io/badge/version-1.0.0-22d3ee) ![Platform](https://img.shields.io/badge/平台-爱奇艺%20·%20腾讯%20·%20优酷%20·%20B站%20·%20芒果-ff9f43) ![License](https://img.shields.io/badge/license-MIT-8b91a5)

## 安装

在 MoviePilot 后台 **设置 → 插件 → 市场管理** 中添加第三方市场：

```
https://github.com/zcsky123/MoviePilot-Plugins
```

刷新市场后安装 **「追剧助手」**，启用即用——推送走 MoviePilot 通知中心（沿用你已配置的消息通道），插件本身**零推送配置、零第三方依赖**。

## 功能一览

| 功能 | 说明 |
|---|---|
| 🔍 五平台搜索订阅 | 爱奇艺 / 腾讯视频 / 优酷 / B站 / 芒果TV，自动过滤花絮、解说、单集切片，只留正剧 |
| 📺 每日检查推送 | 新集 / 完结 / 断更提醒，多剧合并一条卡片：剧名 + 最新进度 + 更新时刻；支持静默时段（23:00-08:00 暂存晨发合并） |
| ⏰ 官方排播时刻 | 爱奇艺官方更新策略文案优先（如「VIP会员每日18:00更新」），其余平台按历史更新日推算星期规律 |
| 📅 追剧日历 / ICS | 插件页内置日历视图；ICS 端点可直接订阅到手机日历 |
| 🆕 豆瓣新剧发现 | 每日对比热播榜（frodo 签名接口 + 网页版自动回退），支持评分阈值 / 关键词黑名单 / 每日上限，可选**自动追剧** |
| 🎬 待映倒计时 | 豆瓣「即将上映」，上映当日推送提醒 |
| 📊 周报 | 每周一推送未来 7 天追剧看点 |
| 💬 远程命令 | `/dt_today` 今天有什么更新 · `/dt_check` 立即检查 · `/dt_search 剧名` 搜索即订阅 |
| 🤖 智能体工具 | 注册「今天有什么更新」工具，AI 对话里直接问追剧进度 |
| 📱 iOS 快捷指令 | `GET /api/v1/plugin/DramaTracker/quick_add?apikey=...&name=剧名&platform=爱奇艺` 一键订阅 |
| ⚙️ 工作流动作 | `追剧检查推送`，可串接进任意工作流 |

## 远程命令示例

| 命令 | 效果 |
|---|---|
| `/dt_today` | 回复今日档期与全部追剧进度 |
| `/dt_check` | 立即执行一次检查并按配置推送 |
| `/dt_search 庆余年` | 搜索并自动订阅最佳候选，同时列出其他平台候选 |

## 插件索引

| 插件 | 版本 | 说明 |
|---|---|---|
| DramaTracker | v1.0.0 | 首次发布：五平台搜索订阅、每日检查推送、排播日历、静默时段、豆瓣新剧发现与自动追剧、待映倒计时、周报、ICS、远程命令与智能体工具 |

版本历史见 [package.v3.json](package.v3.json)。

## 技术说明

- 遵循 MoviePilot V3 插件合同：Vuetify JSON 渲染、宿主内置存储（save_data）/通知（post_message）/调度（CronTrigger），无自建数据库与服务
- 零第三方依赖（httpx 为宿主自带），导入期无副作用，stop_service 可重复安全释放
- 平台接口遇风控（403/空结果）时错误原因透传到前端展示，不静默吞错
- 豆瓣封面防盗链由内置 `/cover` 代理端点（服务端带 Referer 拉图）解决
- 另有同源桌面版 drama-tracker（Tauri/Rust 独立客户端），独立开发分发

## 反馈

提交 issue 或在 MoviePilot 交流群 @zcsky123。安装后首次使用建议在插件配置页打开「立即运行一次」完成全链路自检。
