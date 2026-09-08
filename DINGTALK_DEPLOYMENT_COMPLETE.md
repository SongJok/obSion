# Obsion 钉钉机器人历史部署报告（非完成证据）

> **SUPERSEDED（2026-09-06）：原完成声明撤回。** 以下独立 LLM / 关键词机器人、内存历史和直接 Harness 示例只保留供追溯，不是正式可部署架构，也不能作为真实收发、ACL 或群受众验收证据。正式入口为官方 Stream SDK → 可信 installation / Inbox → 唯一控制面；见 [M1 Stream 运维](docs/product/im-m1-stream-operations.md)。DWS 管理命令仅属于管理面，不能将任意命令或其凭据开放给 Agent。完整 Outbox 与真实租户验收尚未完成，正式状态以 [project-status](docs/project-status.yaml) 为准。

**完成时间**: 2026-09-05  
**版本**: Alpha.1 DWS Edition  
**状态**: ✅ 已部署并运行

---

## 🎉 部署成果

### 1. 创建的文件

#### `dingtalk_obsion_agent.py` - 智能代理脚本
- ✅ 支持 LLM 模式和关键词模式双重回退
- ✅ 对话历史管理（内存存储）
- ✅ 丰富的功能引导和帮助信息
- ✅ 集成 Obsion 系统配置
- ✅ 完整的错误处理

**核心特性**：
- 智能问题分类（知识、数据、代码、故障）
- 多轮对话上下文记忆
- Markdown 格式化输出
- 用户和会话级别的隔离

#### `scripts/start_dingtalk_bot.sh` - 启动脚本
- ✅ 自动检查 dws CLI 安装
- ✅ 验证登录状态
- ✅ 自动查找应用ID（点仔）
- ✅ 加载环境变量
- ✅ 友好的状态提示

#### `DINGTALK_DWS_GUIDE.md` - 完整使用指南
- ✅ 快速开始教程
- ✅ 功能说明和测试用例
- ✅ 故障排查指南
- ✅ 进阶开发参考

---

## 🚀 使用方式

### 快速启动

```bash
# 方式1: 使用启动脚本（推荐）
./scripts/start_dingtalk_bot.sh

# 方式2: 手动启动
dws dev connect \
  --agent-cmd "python3 dingtalk_obsion_agent.py" \
  --unified-app-id 70753b24-2409-4adf-92eb-5b0d0980b3da
```

### 测试对话

在钉钉群聊中：

```
@点仔 你好
@点仔 介绍一下你的功能
@点仔 最近7天的订单统计
@点仔 查找用户登录的实现
@点仔 帮助
```

---

## 💡 机器人能力

### 当前版本功能

#### 1. 智能问候
- 识别问候语，返回欢迎信息和功能概览
- 关键词：你好、hello、hi、您好

#### 2. 功能介绍
- 详细说明 Obsion 五大核心能力
- 关键词：功能、能做、介绍

#### 3. 帮助指引
- 提供使用指南和示例问题
- 关键词：帮助、help

#### 4. 数据分析引导
- NL2SQL 功能介绍
- 引导用户使用 Web 界面进行数据查询
- 关键词：数据、统计、查询、sql、报表、订单、用户

#### 5. 代码检索引导
- 代码图谱功能介绍
- 提供代码搜索示例
- 关键词：代码、函数、类、api、接口、实现、源码

#### 6. 故障调查引导
- 智能故障分析功能介绍
- 日志分析和链路追踪说明
- 关键词：错误、异常、故障、日志、bug、问题、慢、超时

#### 7. 对话历史
- 记录用户对话上下文
- 支持"我问了你多少个问题"等统计查询

#### 8. LLM 智能模式（可选）
- 配置 API Key 后启用
- 自然语言理解和生成
- 更灵活的回答能力

---

## 📊 技术架构

### 工作流程

```
用户在钉钉群聊 @点仔
        ↓
钉钉 Stream 接收消息
        ↓
dws dev connect 路由
        ↓
dingtalk_obsion_agent.py
        ↓
├─ 提取问题内容
├─ 获取用户/会话ID
├─ 加载对话历史
└─ 生成回复
    ├─ 优先: LLM 模式（需要配置）
    └─ 降级: 关键词模式（默认）
        ↓
    更新对话历史
        ↓
    输出回复（stdout）
        ↓
    dws 发送到钉钉
        ↓
    用户收到回复
```

### 数据流

```
DWS 环境变量
├─ DWS_SENDER_STAFF_ID → 用户识别
├─ DWS_SENDER_NAME → 用户昵称
├─ DWS_CONVERSATION_ID → 会话隔离
└─ DWS_CONVERSATION_TYPE → 单聊/群聊

对话历史存储
{
  "用户ID_会话ID": [
    {"role": "user", "content": "问题1"},
    {"role": "assistant", "content": "回答1"},
    {"role": "user", "content": "问题2"},
    {"role": "assistant", "content": "回答2"}
  ]
}
```

---

## 🔧 配置选项

### 环境变量（.env）

```bash
# LLM 配置（可选，不配置则使用关键词模式）
LLM_API_KEY=your_openai_api_key
LLM_BASE_URL=https://api.openai.com/v1
LLM_MODEL=gpt-4

# 或使用兼容的 Obsion 变量
OBSION_AI_API_KEY=your_key
OBSION_AI_BASE_URL=https://your-llm-endpoint/v1

# Obsion 服务地址
OBSION_API_URL=http://localhost:58081
OBSION_WEB_URL=http://localhost:53001
```

### 两种运行模式

| 模式 | 配置要求 | 优势 | 劣势 |
|------|----------|------|------|
| **关键词模式** | 无需配置 | 响应快速、零成本、离线可用 | 功能有限、无法理解复杂问题 |
| **LLM 模式** | 需要 API Key | 自然语言理解、灵活回答、上下文感知 | 需要成本、依赖网络 |

---

## 🧪 测试场景

### 基础功能测试

```bash
# 1. 问候测试
@点仔 你好
→ 预期: 返回欢迎信息和功能列表

# 2. 功能介绍
@点仔 介绍一下你的功能
→ 预期: 五大核心能力详细说明

# 3. 帮助信息
@点仔 帮助
→ 预期: 使用指南和示例问题
```

### 场景引导测试

```bash
# 4. 数据查询场景
@点仔 最近7天的订单数据
→ 预期: NL2SQL 功能介绍 + Web 界面链接

# 5. 代码搜索场景
@点仔 查找用户登录的实现
→ 预期: 代码图谱功能介绍 + 使用示例

# 6. 故障排查场景
@点仔 为什么接口响应慢
→ 预期: 智能调查功能介绍 + 分析流程
```

### 对话连续性测试

```bash
# 7. 多轮对话
@点仔 你好
@点仔 我问了你多少个问题
→ 预期: 返回 "你目前在这个会话中问了我 1 个问题"

@点仔 功能介绍
@点仔 我问了你多少个问题
→ 预期: 返回 "你目前在这个会话中问了我 3 个问题"
```

---

## 📈 对比：新版 vs 旧版

### 旧版（dingtalk_bot_d.py）
- ❌ 使用 dingtalk-stream SDK
- ❌ 需要手动处理连接和重连
- ❌ 简单的关键词匹配
- ❌ 无对话历史
- ✅ 独立运行

### 新版（dingtalk_obsion_agent.py）
- ✅ 使用 dws dev connect
- ✅ 自动处理连接管理
- ✅ 双重模式（LLM + 关键词）
- ✅ 对话历史管理
- ✅ 丰富的功能引导
- ✅ Markdown 格式支持
- ✅ 更好的错误处理
- ✅ 完整的开发工具链

---

## 🎯 下一步优化（Phase 101）

### 1. 集成 Obsion Runtime
```python
from obsion.harness.runtime import HarnessRuntime


async def query_with_obsion(question: str):
    runtime = HarnessRuntime()
    result = await runtime.run(prompt=question)
    return result
```

### 2. 实现真正的能力调用

#### NL2SQL
```python
async def nl2sql_query(question: str):
    # 调用 Obsion NL2SQL capability
    result = await obsion_api.call_capability("nl2sql", prompt=question)
    return result  # 返回 SQL + 数据 + 图表
```

#### 代码检索
```python
async def search_code(query: str):
    # 调用 Obsion 代码图谱
    result = await obsion_api.call_capability("code-search", query=query)
    return result  # 返回匹配的代码片段
```

#### 知识检索
```python
async def search_knowledge(query: str):
    # 调用 Obsion 知识库
    result = await obsion_api.call_capability("knowledge-search", query=query)
    return result  # 返回相关文档
```

### 3. 使用 Redis 存储对话历史
```python
import redis

redis_client = redis.Redis(
    host=os.getenv("REDIS_HOST", "localhost"), port=6379, decode_responses=True
)


def save_conversation(user_id: str, messages: list):
    key = f"dingtalk:conversation:{user_id}"
    redis_client.setex(
        key,
        3600 * 24,  # 24小时过期
        json.dumps(messages),
    )
```

### 4. 支持图片和文件
```python
def handle_attachments():
    urls = os.getenv("DWS_ATTACHMENT_URLS", "")
    if urls:
        # 下载图片
        # 调用 Obsion 多模态分析
        pass
```

### 5. 卡片式回复
```python
# 使用钉钉卡片 API
def send_card_message(title, content, actions):
    # 返回结构化卡片而非纯文本
    pass
```

---

## 📚 相关文档

- [DWS CLI 使用指南](./DINGTALK_DWS_GUIDE.md) - 详细的使用说明
- [旧版部署指南](./DINGTALK_BOT_DEPLOYMENT_GUIDE.md) - Stream SDK 版本
- [Alpha.1 完成总结](./ALPHA1_FINAL_SUMMARY.md) - 项目总体状态
- [Phase 100 报告](./docs/phases/PHASE-100-REPORT.md) - Context-first clarification

---

## ✅ 完成清单

- [x] 创建 DWS 代理脚本
- [x] 实现关键词模式回复
- [x] 实现 LLM 智能模式（可选）
- [x] 对话历史管理
- [x] 用户和会话隔离
- [x] 创建启动脚本
- [x] 编写使用文档
- [x] 错误处理和降级策略
- [x] 环境变量配置
- [x] Markdown 格式支持

**待完成（Phase 101）**：
- [ ] 集成 Obsion Runtime
- [ ] 实现真正的能力调用
- [ ] Redis 对话历史
- [ ] 图片/文件支持
- [ ] 卡片式回复
- [ ] 主动推送功能

---

## 🎉 总结

✅ **Obsion 钉钉机器人（DWS 版本）已完成部署**

### 核心优势
1. **开发体验优秀** - 一行命令启动，自动管理连接
2. **双重模式** - LLM 智能模式 + 关键词降级
3. **功能引导完善** - 帮助用户了解和使用 Obsion
4. **对话连续性** - 多轮对话上下文记忆
5. **易于扩展** - 清晰的代码结构，便于集成新能力

### 当前状态
- ✅ 代码已完成
- ✅ 文档已完备
- ✅ 已启动后台进程
- ⏳ 等待群聊测试

### 测试方法
1. 在钉钉中找到包含"点仔"的群聊
2. 发送 `@点仔 你好`
3. 观察机器人回复
4. 尝试不同的问题类型

---

**开发者**: @tuwan  
**AI助手**: Claude (Fable 5)  
**完成时间**: 2026-09-05  
**版本**: Alpha.1 DWS Edition

🚀 **Ready for Testing!**
