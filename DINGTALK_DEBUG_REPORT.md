# 钉钉机器人D仔 - 问题诊断报告

**问题**: 机器人无法回复消息  
**原因**: 钉钉API凭证验证失败

---

## 🔍 诊断结果

### 问题1: 凭证验证失败
```
错误代码: 40096
错误信息: 不合法的appKey或appSecret
```

**可能原因**:
1. App Key 或 App Secret 不正确
2. 应用已被删除或禁用
3. 应用权限未正确配置
4. 使用了测试环境的凭证但API调用的是生产环境

### 问题2: 应用不存在
```
错误代码: 900103
错误信息: 应用不存在
```

---

## 📋 解决方案

### 方案1: 在钉钉开放平台获取正确凭证

1. **登录钉钉开放平台**
   - 访问 https://open-dev.dingtalk.com/
   - 使用管理员账号登录

2. **找到你的应用**
   - 进入 "应用开发" → "企业内部应用"
   - 找到机器人应用

3. **获取凭证**
   ```
   AppKey (Client ID): [从应用基本信息页面复制]
   AppSecret (Client Secret): [从应用基本信息页面复制]
   ```

4. **配置机器人权限**
   - 确保启用了以下权限：
     - ✅ 企业内机器人接收消息
     - ✅ 企业内机器人发送消息
     - ✅ 通讯录只读权限（可选）

5. **发布应用**
   - 确保应用已发布到企业

### 方案2: 使用dws CLI快速创建机器人应用

```bash
# 1. 登录dws
dws auth login --device

# 2. 创建新的机器人应用
dws dev app create \
  --name "Obsion智能助手D仔" \
  --desc "企业智能工作台AI助手" \
  --app-type "H5microapp"

# 3. 配置机器人权限
dws dev app scope add \
  --app-id "your-app-id" \
  --scopes "Contact.User.Read,Chat.Message.ReadWrite"

# 4. 获取凭证
dws dev app get --app-id "your-app-id" --format json | jq '{appKey, appSecret}'
```

### 方案3: 使用现有的测试应用

如果已经有其他可用的钉钉应用：

```bash
# 列出所有应用
dws dev app list --format json

# 选择一个应用并获取凭证
dws dev app get --app-id "选中的应用ID" --format json
```

---

## 🔧 更新凭证后的操作

### 1. 更新环境变量

```bash
# 编辑 .env 文件
nano /Users/tuwan/work/code/obsion/openWork/.env

# 更新以下两行
OBSION_DINGTALK_APP_KEY=新的AppKey
OBSION_DINGTALK_APP_SECRET=新的AppSecret
```

### 2. 同步到 .env.example

```bash
nano /Users/tuwan/work/code/obsion/openWork/.env.example
# 同样更新这两行
```

### 3. 重启机器人

```bash
# 停止旧进程
pkill -f dingtalk_bot_d.py

# 重新启动
cd /Users/tuwan/work/code/obsion/openWork
export OBSION_DINGTALK_APP_KEY=新的AppKey
export OBSION_DINGTALK_APP_SECRET=新的AppSecret
export OBSION_URL=http://localhost:58081
export OBSION_TOKEN=local-development-only-change-me

nohup python3 dingtalk_bot_d.py > dingtalk_bot_d.log 2>&1 &

# 检查日志
tail -f dingtalk_bot_d.log
```

### 4. 验证连接

```bash
# 应该看到类似输出：
# ✅ 机器人已启动，等待消息...
# ✅ WebSocket连接已建立
```

---

## 🎯 临时方案：使用Mock模式测试

如果暂时无法获取正确凭证，可以先用本地Mock模式测试机器人逻辑：

```bash
# 创建Mock测试脚本
cat > test_bot_mock.py << 'EOF'
#!/usr/bin/env python3
import sys
sys.path.insert(0, '/Users/tuwan/work/code/obsion/openWork')
from dingtalk_bot_d import DingTalkBotHandler

# 模拟消息
class MockMessage:
    def __init__(self):
        self.text = type('obj', (object,), {'content': 'Hello D仔'})()
        self.senderId = 'test-user-001'
        self.conversationId = 'test-conversation'

handler = DingTalkBotHandler()

# 测试不同输入
test_messages = [
    "你好",
    "介绍一下你的功能",
    "帮助",
    "什么是obsion",
    "谢谢"
]

for msg in test_messages:
    print(f"\n📩 用户: {msg}")
    response = handler.generate_response(msg)
    print(f"🤖 D仔: {response}")
    print("-" * 50)
EOF

python3 test_bot_mock.py
```

---

## 📞 需要的信息

请提供以下信息以继续配置：

1. **钉钉开放平台登录账号**
   - 是否有管理员权限？
   - 是否可以访问应用管理页面？

2. **现有应用信息**
   - 是否已经创建了机器人应用？
   - 应用名称是什么？
   - 应用状态是否正常？

3. **凭证来源**
   - 当前使用的AppKey从哪里获取的？
   - 是否是测试环境还是生产环境？

---

## 📝 总结

当前状态：
- ❌ 钉钉凭证验证失败
- ❌ 机器人无法连接到钉钉服务器
- ✅ 机器人代码逻辑正常
- ✅ Obsion系统运行正常

**下一步**：获取正确的钉钉应用凭证后即可正常使用。
