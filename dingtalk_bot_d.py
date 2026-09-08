#!/usr/bin/env python3
"""
钉钉机器人D仔 - 基于Stream模式的机器人
"""

import os

import dingtalk_stream
from dingtalk_stream import AckMessage


class DingTalkBotHandler(dingtalk_stream.ChatbotHandler):
    """钉钉机器人消息处理器"""

    def __init__(self):
        super().__init__()
        self.obsion_api_url = os.getenv("OBSION_URL", "http://localhost:58081")
        self.obsion_token = os.getenv("OBSION_TOKEN", "")

    async def process(self, callback: dingtalk_stream.CallbackMessage):
        """处理接收到的消息"""
        incoming_message = callback.data

        # 提取消息内容
        user_text = incoming_message.text.content.strip()
        sender_id = incoming_message.senderId
        conversation_id = incoming_message.conversationId

        print(f"📩 收到消息: {user_text}")
        print(f"👤 发送者: {sender_id}")
        print(f"💬 会话ID: {conversation_id}")

        # 简单的响应逻辑
        response_text = self.generate_response(user_text)

        # 回复消息
        self.reply_text(response_text, incoming_message)

        return AckMessage.STATUS_OK, "OK"

    def generate_response(self, user_text: str) -> str:
        """生成回复内容"""
        user_text_lower = user_text.lower()

        # 简单的关键词匹配
        if "你好" in user_text or "hello" in user_text_lower or "hi" in user_text_lower:
            return """👋 你好！我是Obsion智能助手D仔。

我可以帮助你：
📚 查询企业知识库
📊 分析业务数据
💻 查找代码信息
🔍 调查系统问题

有什么可以帮助你的吗？"""

        elif "功能" in user_text or "features" in user_text_lower:
            return """🤖 我的核心功能：

1️⃣ **知识查询** - 快速找到公司制度、文档、FAQ
2️⃣ **数据分析** - NL2SQL，自动生成数据报表和图表
3️⃣ **代码查询** - 查找函数定义、API接口、依赖关系
4️⃣ **故障调查** - 分析日志、追踪问题根因、关联事件

💡 提示：直接问我问题就可以了，例如：
- "最近7天的订单量"
- "payment服务的支付接口在哪"
- "公司的年假制度"
"""

        elif "帮助" in user_text or "help" in user_text_lower:
            return """❓ 使用帮助：

**如何提问**：
- 直接@我提问即可
- 支持中文和英文
- 尽量描述清楚你的需求

**示例问题**：
📊 "本月销售额是多少？"
📚 "公司的考勤制度"
💻 "user-service的登录接口"
🔍 "为什么昨天系统响应慢？"

**遇到问题**：
联系技术支持：tech@tuwan.com
"""

        elif "obsion" in user_text_lower:
            return """🏢 Obsion 企业智能工作台

Obsion是一个开源的企业Agent运行时和智能工作台，核心特性：

✅ 企业知识检索（文档、Wiki、代码）
✅ 自然语言数据查询（NL2SQL）
✅ 故障智能调查（日志、链路、指标）
✅ 工作流自动化编排
✅ 多渠道集成（钉钉、飞书、企微）

📖 了解更多：https://github.com/your-org/obsion
"""

        elif "谢谢" in user_text or "thanks" in user_text_lower or "感谢" in user_text:
            return "不客气！很高兴能帮到你 😊 有其他问题随时找我~"

        else:
            # 默认回复
            return f"""🤔 我理解你说的是："{user_text}"

目前我还在学习中，对于复杂问题，我会：
1. 连接Obsion AI引擎进行分析
2. 查询相关知识库和数据
3. 给出结构化的回答

💡 你也可以试试问我：
- "介绍一下你的功能"
- "帮助"
- "你好"
"""


def main():
    """主函数"""
    # 从环境变量获取配置
    client_id = os.getenv("OBSION_DINGTALK_APP_KEY", "")
    client_secret = os.getenv("OBSION_DINGTALK_APP_SECRET", "")

    if not client_id or not client_secret:
        print("❌ 错误：未设置钉钉应用凭证")
        print("请设置环境变量：")
        print("  export OBSION_DINGTALK_APP_KEY=your_app_key")
        print("  export OBSION_DINGTALK_APP_SECRET=your_app_secret")
        return

    print("🤖 启动钉钉机器人D仔...")
    print(f"📱 App Key: {client_id}")
    print(f"🔗 Obsion API: {os.getenv('OBSION_URL', 'http://localhost:58081')}")
    print("")

    # 创建Stream客户端
    credential = dingtalk_stream.Credential(client_id, client_secret)
    client = dingtalk_stream.DingTalkStreamClient(credential)

    # 注册机器人消息处理器
    client.register_callback_handler(dingtalk_stream.ChatbotMessage.TOPIC, DingTalkBotHandler())

    print("✅ 机器人已启动，等待消息...")
    print("💡 提示：在钉钉群中@机器人发送消息进行测试")
    print("")

    # 启动客户端
    client.start_forever()


if __name__ == "__main__":
    main()
