# fund-assistant 小程序版（MVP）

这是从现有 H5 版本拆分出的微信小程序骨架，目标是先跑通：

- 持仓管理
- 实盘行动指南（简版）
- 白话研判（简版）
- 热点事件加载（远程 + 本地回退）

## 目录结构

- `app.js / app.json / app.wxss`：小程序入口
- `pages/dashboard`：首页（行动指南 + 白话研判 + 事件）
- `pages/holdings`：持仓增删
- `pages/settings`：数据源设置与连通性测试
- `utils/api.js`：远程拉取 `hot_events.json`，失败自动回退
- `utils/storage.js`：本地存储封装
- `utils/advisor.js`：MVP 研判逻辑

## 快速运行

1. 打开微信开发者工具
2. 选择「导入项目」
3. 项目目录选择：`fund-assistant/miniprogram`
4. AppID：
   - 调试可先用 `touristappid`
   - 生产请替换为你自己的小程序 AppID
5. 点击编译

## 远程数据说明

默认尝试访问：

`https://allenshen-svg.github.io/fund-assistant/data/hot_events.json`

如果请求失败，会自动回退到 `data/fallback-hot-events.js`。

## 上线前必做

1. 在微信公众平台配置 request 合法域名（HTTPS）
2. `project.config.json` 里替换真实 AppID
3. 按需把 `utils/advisor.js` 替换为你 H5 的完整策略逻辑

## 下一步建议

- 把 H5 的完整策略模块拆成纯函数，逐步迁入 `utils/`
- 引入云函数承接需要长期运行/重计算的逻辑（回测、RL训练）
- 把雷达图改为 Canvas 组件
