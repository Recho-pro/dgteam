# 微信服务号接入说明

`dgteam` 现在已经有一套正式的微信服务号桥接模块，负责把公众号消息接到本地查询引擎，并返回适合微信对话框的摘要结果。

## 最低配置

- `DGTEAM_WECHAT_OFFICIAL_ENABLED=true`
- `DGTEAM_WECHAT_OFFICIAL_APP_ID=...`
- `DGTEAM_WECHAT_OFFICIAL_APP_SECRET=...`
- `DGTEAM_WECHAT_OFFICIAL_TOKEN=...`
- `DGTEAM_WECHAT_OFFICIAL_ENCODING_AES_KEY=...`
- `DGTEAM_WECHAT_OFFICIAL_CALLBACK_PATH=/wechat/official/callback`

## 当前支持

1. `GET` 回调验证
2. `POST` 安全模式消息解密
3. 文字查询
4. 歧义候选 + 回复数字继续
5. 图片消息异步入队
6. 订阅欢迎语
7. 菜单点击事件回复
8. 默认菜单发布 CLI

## 默认菜单

当前默认菜单结构：
- `查行情`
- `热门机型`
- `DG团队`

示例命令：

```powershell
python -m dgteam.integrations.wechat_official.menu_cli --show-default
python -m dgteam.integrations.wechat_official.menu_cli --publish-default --base-url https://dgtdnb.com
python -m dgteam.integrations.wechat_official.menu_cli --get-current
```

## 图片识别 Worker

图片查询已经进入正式异步 worker 方案。

推荐默认模型：
- 主模型：`qwen/qwen3-vl-32b-instruct`
- 兜底模型：`qwen/qwen3-vl-235b-a22b-instruct`

推荐默认预处理参数：
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_TIMEOUT_SECONDS=45`
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_MAX_EDGE_PX=960`
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_MAX_BYTES=240000`
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_JPEG_QUALITY=70`

配套环境变量：
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_WORKER_ENABLED=true`
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_API_KEY=...`
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_PRIMARY_MODEL=qwen/qwen3-vl-32b-instruct`
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_FALLBACK_MODEL=qwen/qwen3-vl-235b-a22b-instruct`
- `DGTEAM_WECHAT_OFFICIAL_IMAGE_POLL_INTERVAL_SECONDS=8`

本地跑一轮 worker：

```powershell
python -m dgteam.integrations.wechat_official.worker_cli --run-once
```

PowerShell 入口：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_wechat_official_worker.ps1
```

## 说明

- AI 只负责识别机型、容量、颜色、版本等结构化信息。
- 价格永远只来自 `dgteam` 数据库和算法。
- 公众号里只返回摘要，复杂颜色和容量分支继续落到网页查询页。

## 进一步阅读

- [公众号查询模块架构方案](C:\Users\somehow\Documents\Playground\dgteam\docs\WECHAT_OFFICIAL_ARCHITECTURE.md)
