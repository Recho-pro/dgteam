# 公众号查询模块架构方案

这份方案把公众号查询能力拆成 4 层，目标不是只把当前文字查询跑通，而是给后面的图片查询、候选多轮确认、菜单、欢迎语、异步 AI 识别留出稳定扩展位。

## 目标

- 保持公众号回调始终轻量、快速、稳定
- 让文字查询和图片查询共享同一套 `dgteam` 行情引擎
- 让图片识别可以独立演进，不污染同步回调
- 让回复文案和行情映射逻辑集中管理

## 四层拆分

### 1. Message Ingress

职责：
- 接公众号 `GET` 验证请求
- 接公众号 `POST` 回调
- 验签、解密、XML 解析
- 统一输出 `WechatOfficialInboundMessage`
- 统一加密被动回复 XML

当前代码：
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\ingress.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\ingress.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\app.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\app.py)

原则：
- 这一层不碰业务查询逻辑
- 这一层不直接做图片识别
- 目标是在微信时限内稳定完成协议处理

### 2. Conversation Workflow

职责：
- 文本消息分流
- 数字回复继续选择
- 图片消息转任务
- 订阅事件、菜单点击事件处理
- 管理短上下文 session

当前代码：
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\workflow.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\workflow.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\session_store.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\session_store.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\models.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\models.py)

原则：
- Workflow 只编排，不直接耦合底层协议
- Workflow 不直接做 AI 推理
- Workflow 只知道“识别任务已入队”

### 3. Recognition Workers

职责：
- 从识别队列取图片任务
- 下载原图、做哈希缓存、做模型路由
- 调用 AI 识别电商详情页截图
- 必要时升级到更强模型兜底
- 输出结构化识别结果

当前代码：
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\recognition_queue.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\recognition_queue.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\recognition_worker.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\recognition_worker.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\worker_cli.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\worker_cli.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\image_recognizer.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\image_recognizer.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\core\openrouter.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\core\openrouter.py)

推荐模型路线：
- 主模型：`qwen/qwen3-vl-32b-instruct`
- 兜底模型：`qwen/qwen3-vl-235b-a22b-instruct`

工程原则：
- AI 只负责识别机型、容量、颜色、版本
- AI 不直接决定行情价格
- 图片识别必须异步，不塞进公众号同步回调

### 4. Market Response Layer

职责：
- 调 `dgteam` 查询引擎
- 处理无结果、歧义、命中结果
- 生成适合公众号的简洁回复
- 把复杂结果收敛到网页入口

当前代码：
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\response_layer.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\response_layer.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\result_dispatcher.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\result_dispatcher.py)
- [C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\formatter.py](C:\Users\somehow\Documents\Playground\dgteam\src\dgteam\integrations\wechat_official\formatter.py)

原则：
- 价格始终只来自 `dgteam` 数据库和算法
- 公众号只回摘要，不回整页复杂明细
- 复杂容量、颜色分支继续走 H5 页面

## 当前状态目录

公众号状态目录默认在：
- `runtime/local/wechat_official/state`

其中包括：
- `sessions/`
- `recognition/inbox/`
- `recognition/queued/`
- `recognition/processing/`
- `recognition/completed/`
- `recognition/failed/`
- `recognition/cache/`

## 当前已落地能力

- 安全模式回调验证
- 安全模式消息解密与加密
- 文字查询
- 候选歧义回复
- 回复数字继续查询
- 图片消息异步入队
- 识别 worker
- 图片识别结果回填公众号对话

## 现在最值得继续做的

### Phase 1

- 把图片 worker 正式部署到云端
- 建立失败重试和任务清理
- 跑真实公众号图片联调

### Phase 2

- 优化弱命中文本查询体验
- 加强图片识别后的候选确认
- 增加任务状态和识别耗时监控

### Phase 3

- 接菜单
- 接欢迎语
- 做用户行为统计
- 进一步支持更复杂的多轮追问

## 风险控制

- 同步回调永远不做重推理
- Worker 失败不影响公众号回调返回 `200`
- AI 只做识别，不做报价
- 价格回复只来自 `dgteam` 查询层
