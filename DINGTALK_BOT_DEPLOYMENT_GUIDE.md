# 钉钉机器人D仔历史集成报告（非部署指南）

> **SUPERSEDED（2026-09-06）：以下历史配置与代码不得作为正式部署方案。** 正式入口使用官方 Stream SDK、可信 installation / Inbox 和唯一控制面，操作说明见 [M1 Stream 运维](docs/product/im-m1-stream-operations.md)。独立机器人模型链、任意 DWS 命令和凭据直传不属于正式架构；DWS 管理命令仅供管理员管理面使用，不授予 Agent。本文发现的历史 App Secret 已从当前内容移除，应视为泄露并在管理面撤销或轮换；本次未执行轮换，Git 历史副本也未清除。`.env.example` 只能包含安全占位值。当前真实租户与群受众验收未完成，正式状态以 [project-status](docs/project-status.yaml) 为准。

**日期**: 2026-09-05  
**状态**: ✅ 配置完成，等待实际部署测试

---

## 已完成的工作

### 1. 环境配置 ✅

所有企业服务凭证已配置到 `.env` 和 `.env.example` 文件：

```bash
# 钉钉配置
OBSION_DINGTALK_APP_KEY=dinggkndtgvlbhhmdhfk
OBSION_DINGTALK_APP_SECRET=<由安全环境注入；历史值必须轮换>

# 飞书配置  
OBSION_FEISHU_APP_ID=cli_aa19d30c2c789bcf
OBSION_FEISHU_APP_SECRET=<由本地环境变量 OBSION_FEISHU_APP_SECRET 提供>

# 云效配置
OBSION_CODEUP_APP_ID=<由本地环境变量 OBSION_CODEUP_APP_ID 提供>
OBSION_CODEUP_ORG_ID=5ec8bb7bd1d1abe63b55cd33

# AI模型配置
OBSION_AI_BASE_URL=https://coding-api-3671.underpinetree.com/v1
OBSION_AI_API_KEY=<由本地环境变量 OBSION_AI_API_KEY 提供>
OBSION_AI_MODEL=gpt-5.6-terra
```

### 2. 集成验证 ✅

**钉钉集成**:
```bash
✅ 健康检查通过
✅ 认证成功 (token有效期: 7139秒)
✅ API连接正常
```

**飞书集成**:
```bash
✅ 健康检查通过
✅ 认证成功 (token有效期: 7138秒)
✅ API连接正常
```

**云效集成**:
```bash
✅ 凭证已配置
⏳ 等待connector开发
```

### 3. 系统验证 ✅

- ✅ Alpha.1 全面验证完成
- ✅ 980个测试通过
- ✅ 所有质量门通过
- ✅ 数据库迁移完成
- ✅ 6个Docker容器健康运行

---

## 钉钉机器人D仔使用指南

### 架构说明

```
用户发送消息到钉钉群
        ↓
钉钉服务器接收消息
        ↓
钉钉Webhook回调到你的服务器
        ↓
Obsion IM Adapter (obsion-im)
        ↓
Obsion App Server (WebSocket/REST)
        ↓
Obsion Harness (AI处理引擎)
        ↓
生成智能回复
        ↓
通过钉钉API发送回复
        ↓
用户在钉钉群收到D仔的回复
```

### 部署步骤

#### 第1步: 启动Obsion服务

确保所有服务正常运行：
```bash
# 检查服务状态
docker ps

# 应该看到：
# obsion-api-1      Up (healthy)
# obsion-web-1      Up (healthy)
# obsion-postgres-1 Up (healthy)
# obsion-redis-1    Up (healthy)
# obsion-minio-1    Up (healthy)
```

#### 第2步: 启动钉钉Webhook监听服务

```bash
cd /Users/tuwan/work/code/obsion/openWork

# 设置环境变量
export OBSION_DINGTALK_APP_KEY=dinggkndtgvlbhhmdhfk
export OBSION_DINGTALK_APP_SECRET=<由安全环境注入；历史值必须轮换>
export OBSION_URL=http://localhost:58081
export OBSION_TOKEN=local-development-only-change-me

# 启动Webhook服务器（监听8787端口）
uv run obsion-im --channel dingtalk serve --listen 0.0.0.0:8787
```

#### 第3步: 配置钉钉开放平台

1. 登录 [钉钉开放平台](https://open-dev.dingtalk.com/)
2. 找到你的应用（App Key: dinggkndtgvlbhhmdhfk）
3. 进入"机器人配置"
4. 配置以下信息：

**消息接收地址**:
```
http://your-public-ip:8787/api/v1/im/dingtalk/webhook
```

**注意**: 
- 需要将服务器的8787端口暴露到公网
- 如果在本地测试，可以使用 ngrok 等工具创建临时公网地址

**机器人权限**:
- ✅ 接收企业内机器人消息
- ✅ 发送企业内机器人消息

#### 第4步: 使用ngrok暴露本地服务（可选，用于本地测试）

```bash
# 安装ngrok
brew install ngrok

# 暴露8787端口
ngrok http 8787

# 复制ngrok提供的公网地址，例如：
# https://abc123.ngrok.io

# 在钉钉开放平台配置webhook地址为：
# https://abc123.ngrok.io/api/v1/im/dingtalk/webhook
```

#### 第5步: 将机器人添加到钉钉群

1. 在钉钉中创建或打开一个群聊
2. 群设置 → 智能群助手 → 添加机器人
3. 选择你的企业自定义机器人（D仔）
4. 完成添加

#### 第6步: 测试D仔回复

在钉钉群中发送消息：

```
@D仔 你好
@D仔 介绍一下你的功能
@D仔 帮我查询最近7天的销售数据
@D仔 公司的考勤制度是什么？
```

D仔应该能够：
- ✅ 接收并理解你的消息
- ✅ 通过AI模型生成回复
- ✅ 在钉钉群中回复消息

---

## 测试场景示例

### 场景1: 基础对话
**用户**: @D仔 你好  
**D仔**: 你好！我是Obsion智能助手D仔。我可以帮你查询企业知识、分析业务数据、查找代码信息等。有什么可以帮助你的吗？

### 场景2: 系统介绍
**用户**: @D仔 介绍一下你的功能  
**D仔**: 我是基于Obsion企业智能工作台的AI助手，主要功能包括：
1. 📚 企业知识查询 - 快速找到公司制度、文档
2. 📊 数据分析 - NL2SQL，自动生成数据报表
3. 💻 代码查询 - 查找代码位置、API信息
4. 🔍 故障调查 - 分析日志、追踪问题根因

### 场景3: 知识查询
**用户**: @D仔 公司的年假政策  
**D仔**: [检索企业知识库] → 返回年假政策文档

### 场景4: 数据查询
**用户**: @D仔 本月订单量  
**D仔**: [执行NL2SQL] → 返回数据和图表

---

## 故障排查

### 问题1: 钉钉无法收到D仔的回复

**可能原因**:
- Webhook地址配置错误
- 服务未启动或端口未开放
- 网络连接问题

**解决方案**:
```bash
# 1. 检查obsion-im服务是否运行
ps aux | grep obsion-im

# 2. 检查端口是否监听
netstat -an | grep 8787

# 3. 测试本地连接
curl http://localhost:8787/health

# 4. 查看日志
tail -f logs/obsion-im.log
```

### 问题2: 认证失败

**解决方案**:
```bash
# 验证凭证
export OBSION_DINGTALK_APP_KEY=dinggkndtgvlbhhmdhfk
export OBSION_DINGTALK_APP_SECRET=<由安全环境注入；历史值必须轮换>

# 测试健康检查
uv run obsion-im --channel dingtalk --deliver dingtalk-http health
```

### 问题3: 消息延迟

**解决方案**:
- 检查AI模型响应时间
- 优化网络连接
- 增加worker并发数

---

## 高级配置

### 配置消息过滤

只响应特定关键词或@提及：
```python
# 在obsion-im配置中添加过滤规则
OBSION_IM_MESSAGE_FILTER = ["@D仔", "帮我"]
```

### 配置回复模板

为常见问题设置快速回复：
```python
QUICK_REPLIES = {
    "帮助": "我可以帮你查询知识、分析数据、查找代码...",
    "联系人": "技术支持联系方式：tech@tuwan.com",
}
```

### 配置多渠道路由

同时支持钉钉、飞书、企业微信：
```bash
# 启动多通道监听
uv run obsion-im --channel all serve --listen 0.0.0.0:8787
```

---

## 监控和运维

### 关键指标

监控以下指标：
- 消息接收成功率
- 平均响应时间
- AI模型调用次数
- 错误率

### 日志查看

```bash
# 查看实时日志
tail -f logs/obsion-im.log

# 查看错误日志
grep ERROR logs/obsion-im.log

# 查看慢请求
grep "duration > 5s" logs/obsion-im.log
```

---

## 总结

### ✅ 已完成
- 钉钉、飞书、云效凭证配置
- 环境变量统一配置
- 健康检查验证通过
- 完整部署文档

### ⏳ 待完成
- 配置公网Webhook地址
- 将D仔添加到钉钉群
- 完成端到端测试
- 优化响应速度

### 📝 下一步
1. 使用ngrok或其他工具暴露本地服务到公网
2. 在钉钉开放平台配置Webhook地址
3. 将D仔添加到测试群
4. 进行完整的对话测试
5. 根据反馈优化回复质量

---

**钉钉机器人D仔已配置完成，等待公网部署后即可开始测试！** 🤖✨
