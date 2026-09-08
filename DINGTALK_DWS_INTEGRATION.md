# 钉钉 DWS 历史集成记录（管理面参考，非产品入口）

> **SUPERSEDED（2026-09-06）：下文方案与状态未经当前契约验证。** 正式机器人使用官方 Stream SDK、可信 installation / Inbox 和唯一控制面，见 [M1 Stream 运维](docs/product/im-m1-stream-operations.md)。本文的 CLI 版本、输入输出协议、登录有效期、Webhook 与 SDK 草图不得直接当作现有产品实现。DWS 管理命令仅属于管理员管理面，禁止向 Agent 暴露任意命令、profile 切换、权限管理、应用发布或登录凭据。需要业务 DWS 时必须由可信 Broker 暴露固定能力及类型化参数，每次经过 Capability Gateway / Policy。历史登录和发送记录不构成当前授权或真实租户验收。

## DWS 命令行工具

DWS (DingTalk Workspace CLI) 是钉钉官方的开发者命令行工具，用于本地开发和调试钉钉机器人。

### 当前配置

根据 `worker.txt` 和 dws 测试结果：

```bash
# 企业信息
企业名称: zziv
企业 ID: ding1309fda8c58033082e2feadba6d9041e
用户: Joony (管理员)
用户 ID: 013526256671757557

# 机器人应用
1. D仔 (测试机器人)
   Unified App ID: 1bfc6ab7-2a58-483d-a5d8-499dff52d682
   描述: 用于测试的机器人

2. 测试
   Unified App ID: ee0ab94d-300d-47b2-a8fe-c95248db3316
   描述: 机器人测试
```

### 验证步骤

#### 1. 登录认证

```bash
# 登录钉钉 (需要扫码)
dws auth login

# 检查登录状态
dws auth status

# 查看当前用户信息
dws contact user get-self --format json
```

#### 2. 查看应用列表

```bash
# 列出所有机器人应用
dws dev app list --format json
```

#### 3. 本地调试

```bash
# 使用 D仔 机器人进行本地调试
dws dev connect \
  --agent-cmd "python /path/to/your/bot_agent.py" \
  --unified-app-id 1bfc6ab7-2a58-483d-a5d8-499dff52d682

# 或者使用 Obsion 的钉钉机器人
dws dev connect \
  --agent-cmd "python dingtalk_bot_d.py" \
  --unified-app-id 1bfc6ab7-2a58-483d-a5d8-499dff52d682
```

### Obsion 集成方案

#### 方案 1: 使用 DWS Stream 模式 (推荐)

```python
# dingtalk_bot_obsion.py
import asyncio
import sys
import json
from obsion_sdk import ObsionClient


async def handle_message(message: dict):
    """处理钉钉消息"""
    text = message.get("text", "")

    # 调用 Obsion API
    client = ObsionClient(base_url="http://localhost:58081/api/v1")

    # 创建 Run
    response = await client.create_run(
        prompt=text, context={"source": "dingtalk", "user_id": message.get("staffId")}
    )

    return response["answer"]


def main():
    """DWS 要求的标准输入输出格式"""
    for line in sys.stdin:
        try:
            message = json.loads(line)

            # 异步处理
            answer = asyncio.run(handle_message(message))

            # 输出结果 (DWS 格式)
            result = {"type": "text", "content": answer}
            print(json.dumps(result, ensure_ascii=False))
            sys.stdout.flush()

        except Exception as e:
            error_result = {"type": "text", "content": f"处理失败: {str(e)}"}
            print(json.dumps(error_result, ensure_ascii=False))
            sys.stdout.flush()


if __name__ == "__main__":
    main()
```

#### 方案 2: 使用 Webhook 模式

```python
# Obsion Control Plane 中添加钉钉 Webhook 处理
# services/control-plane/src/obsion/im/dingtalk_webhook.py

from fastapi import APIRouter, Request
from obsion.application.runs import create_run

router = APIRouter(prefix="/webhooks/dingtalk")


@router.post("/message")
async def handle_dingtalk_message(request: Request):
    """处理钉钉 Webhook 消息"""
    data = await request.json()

    # 提取消息内容
    text = data.get("text", {}).get("content", "")
    user_id = data.get("senderId")

    # 创建 Obsion Run
    run = await create_run(
        prompt=text,
        context={"source": "dingtalk", "user_id": user_id, "conv_id": data.get("conversationId")},
    )

    # 返回钉钉格式的响应
    return {"msgtype": "text", "text": {"content": run.answer}}
```

### 启动命令

#### 使用 DWS Stream 模式

```bash
# 1. 启动 Obsion 服务
docker compose up -d

# 2. 启动 DWS Stream 连接
dws dev connect \
  --agent-cmd "python dingtalk_bot_obsion.py" \
  --unified-app-id 1bfc6ab7-2a58-483d-a5d8-499dff52d682 \
  --agent-workdir /Users/tuwan/work/code/obsion/openWork
```

#### 使用 Webhook 模式

```bash
# 1. 启动 Obsion 服务 (包含 Webhook 端点)
docker compose up -d

# 2. 使用 ngrok 暴露本地服务
ngrok http 58081

# 3. 在钉钉开放平台配置 Webhook URL
# https://your-ngrok-url.ngrok.io/webhooks/dingtalk/message
```

### 测试步骤

1. **确认 DWS 已登录**
   ```bash
   dws auth status
   ```

2. **查看机器人列表**
   ```bash
   dws dev app list --format json
   ```

3. **启动本地调试**
   ```bash
   dws dev connect \
     --agent-cmd "python dingtalk_bot_d.py" \
     --unified-app-id 1bfc6ab7-2a58-483d-a5d8-499dff52d682
   ```

4. **在钉钉群中测试**
   - 打开钉钉客户端
   - 在群里 @D仔
   - 发送测试消息

### 配置文件更新

更新 `.env` 中的钉钉配置：

```bash
# 钉钉配置 (DWS Stream 模式不需要 AppKey/Secret)
OBSION_DINGTALK_UNIFIED_APP_ID=1bfc6ab7-2a58-483d-a5d8-499dff52d682
OBSION_DINGTALK_CORP_ID=ding1309fda8c58033082e2feadba6d9041e
OBSION_DINGTALK_MODE=dws_stream  # dws_stream 或 webhook

# 如果使用 Webhook 模式，需要配置：
# OBSION_DINGTALK_APP_KEY=dingonkbr6jzpcwjbnpp
# OBSION_DINGTALK_APP_SECRET=<由本地环境变量 OBSION_DINGTALK_APP_SECRET 提供>
```

### DWS 常用命令

```bash
# 认证
dws auth login                    # 登录
dws auth logout                   # 登出
dws auth status                   # 查看状态

# 应用管理
dws dev app list                  # 列出所有应用
dws dev app info <app-id>         # 查看应用详情

# 本地调试
dws dev connect \                 # 建立 Stream 连接
  --agent-cmd <command> \
  --unified-app-id <id> \
  --agent-workdir <path>          # 可选: 指定工作目录

# 联系人
dws contact user get-self         # 获取当前用户信息
dws contact dept list             # 列出部门

# AI 搜索
dws aisearch <query>              # AI 搜索
```

### 优势

使用 DWS Stream 模式的优势：

1. **无需公网 IP** - Stream 建立长连接，无需配置 Webhook URL
2. **本地调试方便** - 实时看到日志输出
3. **自动重连** - 网络断开自动重连
4. **权限简单** - 通过扫码授权，无需管理 AppKey/Secret

### 注意事项

1. **DWS Stream 仅用于开发调试**
   - 生产环境应该使用 Webhook 或发布到钉钉云端

2. **保持 DWS 登录状态**
   - Token 有效期 30 天
   - 过期后需要重新 `dws auth login`

3. **Agent 命令必须从 stdin 读取并输出到 stdout**
   - 输入格式: JSON (每行一条消息)
   - 输出格式: JSON (DWS 规定的格式)

### 下一步

1. ✅ DWS 已登录并验证
2. ✅ 机器人列表已确认
3. 🚧 创建 `dingtalk_bot_obsion.py` 连接 Obsion API
4. 🚧 测试 Stream 模式消息收发
5. 🚧 (可选) 实现 Webhook 模式作为生产方案

---

**文档创建**: 2026-09-05  
**参考**: 你提供的 dws 命令测试输出
