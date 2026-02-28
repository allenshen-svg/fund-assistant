# 基金决策助手 - 微信小程序版

从 H5 Web 应用完整移植的微信小程序版本，暗色主题，功能对齐。

## 功能模块

### 📊 首页 (Dashboard)
- **实时指数行情** — 上证、深证、创业板、沪深300、黄金、白银、有色金属
- **持仓估值概览** — 12只内置基金实时估值 + 涨跌幅
- **行动指南** — 基于板块热力自动生成加仓/减仓/持有建议
- **板块热力图** — 可视化展示各板块温度
- **热点事件** — 最新市场事件及影响分析

### 📡 舆情分析 (Sentiment)
- **板块热力图** — 温度条形图可视化
- **市场展望** — AI 生成的市场研判
- **事件筛选** — 按利好/利空/政策/科技/地缘/商品分类
- **详细分析** — 每条事件的影响板块、建议操作

### 💼 持仓管理 (Holdings)
- **实时估值** — 显示每只基金当日估值和涨跌
- **手动添加** — 输入代码/名称/类型
- **快速选择** — 从内置基金库一键添加
- **恢复默认** — 一键恢复 12 只内置基金

### ⚙️ 设置 (Settings)
- **数据源切换** — 远程 GitHub Pages / 本地回退
- **连接测试** — 验证远程数据可用性
- **缓存管理** — 清除所有本地数据
- **上线清单** — 发布前必做事项提醒

## 目录结构

```
miniprogram/
├── app.js              # 全局配置（基金库、节假日、指数代码）
├── app.json            # 页面路由、TabBar、窗口样式
├── app.wxss            # 全局暗色主题样式
├── project.config.json # 微信开发者工具配置
├── images/             # TabBar 图标
├── data/
│   └── fallback-hot-events.js  # 离线回退数据
├── pages/
│   ├── dashboard/      # 首页（指数+持仓+行动指南+热点）
│   ├── sentiment/      # 舆情分析（热力图+事件+展望）
│   ├── holdings/       # 持仓管理（增删+估值）
│   └── settings/       # 设置（数据源+缓存+关于）
└── utils/
    ├── api.js          # 网络请求（热点、指数、基金估值、板块资金流）
    ├── storage.js      # 本地存储（持仓、设置、自选）
    ├── market.js       # 市场工具（交易日判断、格式化）
    └── advisor.js      # 研判引擎（热力匹配、行动建议）
```

## 快速运行（详细步骤）

### 前置条件

1. **下载微信开发者工具**
   - 下载地址：<https://developers.weixin.qq.com/miniprogram/dev/devtools/download.html>
   - 选择 **稳定版 Stable Build**，根据你的操作系统（macOS / Windows 64 / Windows 32）点击对应按钮下载
   - 下载完成后双击安装包，按提示安装即可

2. **准备微信账号**
   - 需要一个微信账号用于扫码登录开发者工具
   - 无需任何特殊权限，普通微信号即可

3. **（可选）注册小程序 AppID**（仅真机预览/发布上线时需要）
   - 注册地址：<https://mp.weixin.qq.com/wxopen/waregister?action=step1>
   - 注册流程指引：<https://developers.weixin.qq.com/miniprogram/dev/framework/quickstart/getstart.html#%E7%94%B3%E8%AF%B7%E5%B8%90%E5%8F%B7>
   - 注册完成后，登录 [微信公众平台](https://mp.weixin.qq.com/) → 左侧菜单「开发管理」→「开发设置」→ 复制 **AppID(小程序ID)**

4. **下载本项目代码**
   ```bash
   git clone https://github.com/allenshen-svg/fund-assistant.git
   ```
   或者直接在 GitHub 页面下载 ZIP：<https://github.com/allenshen-svg/fund-assistant/archive/refs/heads/main.zip>

### 第一步：导入项目

1. 打开 **微信开发者工具**，用微信扫码登录
   - 首次打开的界面说明：<https://developers.weixin.qq.com/miniprogram/dev/devtools/page.html>
2. 点击左上角 **「+」→「导入」**
3. 在弹出的对话框中：
   - **目录**：选择本仓库的 `miniprogram` 文件夹（完整路径如 `/path/to/fund-assistant/miniprogram`）
   - **AppID**：
     - 如果只是本地调试预览，填 `touristappid`（游客模式，无需注册）
     - 如果要真机预览或发布上线，填你在微信公众平台获取的小程序 AppID
   - **后端服务**：选择「不使用云服务」
4. 点击 **「确定」** 完成导入

### 第二步：编译预览

1. 导入成功后，开发者工具会自动编译，左侧模拟器中即可看到小程序界面
2. 如果没有自动编译，点击顶部工具栏的 **「编译」** 按钮
3. **重要：关闭域名校验**（否则本地调试时无法请求数据）
   - 点击右上角 **「详情」** → **「本地设置」** → 勾选 ✅ **「不校验合法域名、web-view(业务域名)、TLS 版本以及 HTTPS 证书」**
   - 相关文档：<https://developers.weixin.qq.com/miniprogram/dev/devtools/projectconfig.html>
4. 首次运行时，首页会自动加载：
   - 指数行情（上证、深证、创业板等 7 个指数）
   - 12 只内置基金的实时估值
   - 板块热力图和热点事件

### 第三步：真机预览（可选）

1. 确保使用了真实 AppID（非 touristappid）
2. 在微信公众平台配置合法域名（见下方「上线前必做」第 1 条）
   - 配置入口：登录 <https://mp.weixin.qq.com/> → 左侧「开发管理」→「开发设置」→「服务器域名」→「修改」
3. 点击开发者工具顶部工具栏的 **「预览」** 按钮
4. 用微信扫描生成的二维码
5. 即可在手机上体验完整功能

> ⚠️ 真机预览要求：真实 AppID + 已配置合法域名。详见 [真机调试文档](https://developers.weixin.qq.com/miniprogram/dev/devtools/preview.html)。

### 第四步：发布上线（可选）

1. 确保已完成「上线前必做」中的所有配置
2. 点击顶部工具栏的 **「上传」** 按钮
3. 填写版本号和备注，点击上传
4. 登录 [微信公众平台](https://mp.weixin.qq.com/) → 管理 → 版本管理 → 提交审核
   - 审核规范参考：<https://developers.weixin.qq.com/miniprogram/product/reject.html>
5. 审核通过后，在版本管理页点击「发布」即可上线
   - 发布流程文档：<https://developers.weixin.qq.com/miniprogram/dev/framework/quickstart/release.html>

### 常见问题

| 问题 | 解决方法 |
|------|---------|
| 编译报错「找不到 app.json」 | 确认导入的目录是 `miniprogram` 文件夹，不是上层的 `fund-assistant` |
| 指数行情显示空白 | 检查是否开启了「不校验合法域名」：详情 → 本地设置 → 勾选。[参考文档](https://developers.weixin.qq.com/miniprogram/dev/devtools/projectconfig.html) |
| 基金估值都显示「待开盘」 | 非交易时段（周末/节假日/收盘后）无实时估值，属于正常现象 |
| 热点事件显示「本地回退」 | 远程数据源不可达，小程序自动使用本地样本数据，可在设置页测试连接 |
| 真机预览报域名错误 | 需在 [微信公众平台](https://mp.weixin.qq.com/) 添加 request 合法域名（见下方「上线前必做」） |
| 不知道 AppID 在哪看 | 登录 <https://mp.weixin.qq.com/> → 开发管理 → 开发设置 → AppID(小程序ID) |

## 数据源

| 数据 | 来源 | 说明 |
|------|------|------|
| 板块热力 / 事件 | GitHub Pages | `hot_events.json`，每30分钟更新 |
| 指数行情 | 东方财富 | push2.eastmoney.com |
| 基金估值 | 天天基金 | fundgz.1234567.com.cn |
| 板块资金流 | 东方财富 | 主力净流入数据 |
| 离线回退 | 本地 | `data/fallback-hot-events.js` |

## 上线前必做

1. ✅ 微信公众平台 → 开发管理 → 服务器域名 → 添加 request 合法域名：
   - `https://allenshen-svg.github.io`
   - `https://push2.eastmoney.com`
   - `https://fundgz.1234567.com.cn`
2. ✅ `project.config.json` 替换真实 AppID
3. ✅ 按需调整 `utils/advisor.js` 中的策略参数
