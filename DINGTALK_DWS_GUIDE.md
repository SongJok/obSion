# Obsion 钉钉机器人历史使用指南（DWS 原型，已替代）

> **SUPERSEDED（2026-09-06）：勿按本文部署正式机器人。** 以下 CLI 安装命令、协议示例、关键词/独立 LLM 链和进程内历史是未重新验收的历史原型，不代表当前接口。正式入口见 [M1 Stream 运维](docs/product/im-m1-stream-operations.md)，使用官方 Stream SDK、可信 installation / Inbox 与唯一控制面，不再另建模型链或事实源。DWS 登录、profile、权限和应用管理仅限管理面；业务能力若需 DWS，只能经可信 Broker 提供固定能力和类型化参数，每次通过 Capability Gateway / Policy 鉴权，不能把任意 DWS 命令或登录目录交给 Agent。真实群受众与出站验收仍待完成。

## 概述

本指南介绍如何使用 `dws dev connect` 方式启动和测试 Obsion 钉钉机器人。

### 为什么选择 DWS？

相比传统的 Stream SDK 方式，`dws dev connect` 提供：
- ✅ 自动管理 WebSocket 连接和重连
- ✅ 内置表情反馈（thinking/done）
- ✅ 支持 Markdown 格式回复
- ✅ 自动处理消息路由和会话管理
- ✅ 更简洁的开发体验（无需处理底层协议）

---

## 快速开始

### 1. 安装 DWS CLI

```bash
# macOS
brew install dingtalk-workspace

# 或使用 Go 安装
go install github.com/dingtalk-stream/dws@latest
```

### 2. 登录钉钉

```bash
dws auth login
```

在浏览器中完成扫码授权。

### 3. 启动机器人

使用提供的启动脚本（推荐）：

```bash
./scripts/start_dingtalk_bot.sh
```

或手动启动：

```bash
dws dev connect \
  --agent-cmd "python3 dingtalk_obsion_agent.py" \
  --unified-app-id 70753b24-2409-4adf-92eb-5b0d0980b3da
```

---

## 机器人功能

### 当前版本能力

1. **智能对话** - 基于 LLM 的自然语言理解（可选）
2. **关键词回复** - 离线关键词匹配（无需 LLM）
3. **对话历史** - 多轮对话上下文记忆
4. **功能引导** - 帮助用户了解 Obsion 能力

### 支持的问题类型

#### 📚 企业知识
```
@点仔 公司请假制度是什么
@点仔 技术文档在哪里找
```

#### 📊 数据分析
```
@点仔 最近7天的订单统计
@点仔 按地区统计活跃用户数
```

#### 💻 代码检索
```
@点仔 查找用户登录的实现
@点仔 支付相关的 API 有哪些
```

#### 🔍 故障调查
```
@点仔 为什么接口响应慢
@点仔 最近的错误日志有哪些
```

#### ❓ 系统帮助
```
@点仔 你好
@点仔 功能介绍
@点仔 帮助
```

---

## 配置说明

### 环境变量

在 `.env` 文件中配置：

```bash
# LLM 配置（可选，不配置则使用关键词模式）
LLM_API_KEY=your_api_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4

# Obsion 服务地址
OBSION_API_URL=http://localhost:58081
OBSION_WEB_URL=http://localhost:53001
```

### 两种运行模式

#### 1. 智能模式（需要 LLM）
- 配置 `LLM_API_KEY` 和相关参数
- 支持自然语言理解和多轮对话
- 更灵活的回答能力

#### 2. 关键词模式（无需 LLM）
- 不需要配置 API Key
- 基于关键词匹配预设回复
- 响应快速，成本为零

---

## 测试示例

### 添加机器人到群聊

1. 打开钉钉客户端
2. 进入群聊设置
3. 智能群助手 → 添加机器人
4. 搜索"点仔"
5. 添加到群聊

### 测试对话

**基础测试**
```
@点仔 你好
→ 👋 你好！我是 Obsion 智能助手**点仔**...

@点仔 介绍一下你的功能
→ 🤖 **Obsion 核心能力**...

@点仔 帮助
→ 📖 **使用指南**...
```

**数据查询测试**
```
@点仔 最近7天的订单数据
→ 📊 **数据查询**
   Obsion 的 NL2SQL 功能可以...

@点仔 按省份统计用户数
→ [返回数据分析相关引导]
```

**代码搜索测试**
```
@点仔 查找用户登录的实现
→ 💻 **代码检索**
   Obsion 的代码图谱功能可以...

@点仔 支付接口的代码在哪
→ [返回代码搜索相关引导]
```

**故障排查测试**
```
@点仔 为什么接口响应慢
→ 🔍 **故障调查**
   Obsion 的智能调查功能可以...
```

**多轮对话测试**
```
@点仔 你好
→ [问候]

@点仔 我问了你多少个问题
→ 你目前在这个会话中问了我 **1 个问题**

@点仔 功能介绍
→ [功能列表]

@点仔 我问了你多少个问题
→ 你目前在这个会话中问了我 **3 个问题**
```

---

## 技术细节

### DWS 环境变量

`dws dev connect` 会设置以下环境变量给代理脚本：

- `DWS_SENDER_STAFF_ID` - 发送者的员工ID
- `DWS_SENDER_NAME` - 发送者姓名
- `DWS_CONVERSATION_ID` - 会话ID
- `DWS_CONVERSATION_TYPE` - 会话类型（1=单聊，2=群聊）
- `DWS_ATTACHMENT_URLS` - 附件URL（如果有）

### 代理脚本工作流程

```python
1. 接收用户消息（sys.argv[1:]）
2. 清理 @机器人 标记
3. 获取用户ID和会话ID（从环境变量）
4. 加载对话历史
5. 调用 LLM 或使用关键词匹配
6. 更新对话历史
7. 输出回复到 stdout
```

### 对话历史存储

- 当前使用内存存储（字典）
- 每个用户+会话独立的历史记录
- 限制最近 5 轮对话（10 条消息）
- 生产环境建议使用 Redis

---

## 故障排查

### 问题：机器人无响应

**检查清单：**
1. ✅ 机器人进程是否运行
2. ✅ Stream 连接是否正常（查看日志）
3. ✅ 是否正确 @机器人
4. ✅ 机器人是否已添加到群聊

**查看日志：**
```bash
# dws 会输出详细日志
[connect] 收到 @用户: 消息内容
[connect] agent 已生成回复
[connect] 普通消息已发送
```

### 问题：回复内容不完整

**可能原因：**
- 回复内容过长（超过钉钉限制）
- LLM 生成超时

**解决方案：**
- 在代理脚本中限制 `max_tokens`
- 添加内容截断逻辑

### 问题：LLM 调用失败

**检查：**
1. API Key 是否正确
2. Base URL 是否可访问
3. 模型名称是否正确

**降级方案：**
- 自动降级到关键词模式
- 返回友好的错误提示

---

## 进阶功能

### 添加自定义命令

在 `fallback_response()` 函数中添加：

```python
elif question == '/status':
    return "✅ 机器人运行正常\n" + \
           f"- API: {OBSION_API_URL}\n" + \
           f"- Web: {OBSION_WEB_URL}"
```

### 集成 Obsion API

```python
import httpx


async def query_obsion_api(question: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(f"{OBSION_API_URL}/api/v1/runs", json={"prompt": question})
        return response.json()
```

### 添加图片/文件支持

```python
def handle_attachments():
    urls = os.getenv("DWS_ATTACHMENT_URLS", "")
    if urls:
        # 处理附件
        pass
```

---

## 对比：DWS vs Stream SDK

| 特性 | DWS | Stream SDK |
|------|-----|------------|
| 启动方式 | 一行命令 | 需要编写连接代码 |
| 重连处理 | 自动 | 需要手动实现 |
| 消息路由 | 自动 | 需要手动解析 |
| 表情反馈 | 内置 | 需要调用 API |
| 调试体验 | 实时日志 | 需要自己打印 |
| 适用场景 | 快速开发、测试 | 生产环境、复杂场景 |

---

## 下一步

### 短期优化（Phase 101）
- [ ] 集成 Obsion Harness Runtime
- [ ] 实现真正的知识库检索
- [ ] 添加 NL2SQL 功能
- [ ] 代码检索能力接入
- [ ] 使用 Redis 存储对话历史

### 中期规划
- [ ] 支持图片/文件上传
- [ ] 多模态分析（图表、截图）
- [ ] 工作流触发和状态查询
- [ ] 群聊协作功能

### 长期愿景
- [ ] 主动推送（告警、日报）
- [ ] 个性化学习
- [ ] 企业知识图谱
- [ ] 跨应用集成（飞书、企业微信）

---

## 参考资源

- [DWS CLI 文档](https://open.dingtalk.com/document/dws)
- [钉钉机器人开发指南](https://open.dingtalk.com/document/robots)
- [Obsion 架构文档](./docs/architecture/)
- [Phase 100 报告](./docs/phases/PHASE-100-REPORT.md)

---

**文档更新**: 2026-09-05  
**版本**: Alpha.1 DWS Edition  
**维护**: Obsion Team
