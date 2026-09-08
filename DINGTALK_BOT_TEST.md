# 钉钉机器人（D仔）集成测试

## 配置信息

### 钉钉应用凭证
- **App Key**: dinggkndtgvlbhhmdhfk
- **App Secret**: m7tfoL87bWBZeucw_oF9W9TdXYk2gmmpk5V_8RAwuDE_lhl3IG9RE0PP9Pa4kdXG
- **配置位置**: `.env` 文件中的 `OBSION_DINGTALK_APP_KEY` 和 `OBSION_DINGTALK_APP_SECRET`

### 云效配置
- **App ID**: <由本地环境变量 OBSION_CODEUP_APP_ID 提供>
- **Org ID**: 5ec8bb7bd1d1abe63b55cd33
- **配置位置**: `.env` 文件中的 `OBSION_CODEUP_APP_ID` 和 `OBSION_CODEUP_ORG_ID`

## 测试步骤

### 1. 健康检查
```bash
uv run obsion-im --channel dingtalk --deliver dingtalk-http health
```

### 2. 创建机器人用户绑定
```bash
# 在Obsion系统中创建钉钉机器人用户
curl -X POST http://localhost:58081/api/v1/users \
  -H "Authorization: Bearer local-development-only-change-me" \
  -H "Content-Type: application/json" \
  -d '{
    "email": "dingtalk-bot@tuwan.com",
    "display_name": "钉钉机器人D仔",
    "roles": ["viewer"]
  }'
```

### 3. 发送测试消息
```bash
# 模拟钉钉消息回调
uv run obsion-im --channel dingtalk ingest \
  --sender-id dingtalk-user-001 \
  --text "你好，D仔！请帮我分析一下系统状态"
```

### 4. 启动钉钉Webhook服务器
```bash
# 启动监听钉钉回调的服务器
uv run obsion-im --channel dingtalk serve --listen 0.0.0.0:8787
```

## 钉钉机器人配置要求

### 在钉钉开放平台配置

1. **消息接收地址**:
   ```
   http://your-public-domain:8787/api/v1/im/dingtalk/webhook
   ```

2. **必需的权限**:
   - 企业内机器人发送消息
   - 接收企业内机器人消息

3. **IP白名单**:
   - 添加你的服务器公网IP

### Webhook回调验证

钉钉回调包含以下字段：
- `conversationId`: 会话ID
- `chatbotUserId`: 机器人ID
- `senderNick`: 发送者昵称
- `senderId`: 发送者ID
- `text`: 消息内容

## 消息流程

```
用户 → 钉钉 → Webhook → Obsion IM Adapter → Obsion Harness → AI处理 → 回复生成 → DingTalk API → 用户
```

## 测试场景

### 场景1: 简单问答
**用户**: "你好"
**D仔**: "你好！我是Obsion智能助手D仔，有什么可以帮助你的吗？"

### 场景2: 企业知识查询
**用户**: "公司的考勤制度是什么？"
**D仔**: [从企业知识库检索] → 提供考勤制度信息

### 场景3: 数据查询
**用户**: "最近7天的销售数据"
**D仔**: [NL2SQL] → 生成图表和数据报告

### 场景4: 代码查询
**用户**: "payment-service的支付接口在哪里？"
**D仔**: [Code Graph] → 提供代码位置和关键信息

## 故障排查

### 问题1: 无法收到回调
- 检查Webhook地址是否正确配置
- 检查防火墙和端口是否开放
- 检查钉钉应用权限是否正确

### 问题2: 认证失败
- 验证App Key和App Secret是否正确
- 检查.env文件是否已重新加载

### 问题3: 消息无法发送
- 检查网络连接
- 验证机器人是否在群组中
- 检查API调用配额

## 注意事项

1. **开发环境配置**:
   - 本地测试使用 `OBSION_IM_DELIVER=local_outbox`
   - 线上环境使用 `--deliver dingtalk-http`

2. **安全配置**:
   - 生产环境建议配置 `OBSION_IM_WEBHOOK_SECRET`
   - 使用HTTPS和TLS证书

3. **性能优化**:
   - 异步处理消息
   - 设置合理的超时时间
   - 控制并发请求数量

## 下一步

- [ ] 完成钉钉Webhook公网暴露配置
- [ ] 测试各种消息类型（文本、图片、文件）
- [ ] 配置消息模板
- [ ] 设置自动回复规则
- [ ] 集成企业内部知识库
