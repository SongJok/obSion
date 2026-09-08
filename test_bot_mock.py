#!/usr/bin/env python3
"""钉钉机器人D仔 - 本地Mock测试"""

import sys

sys.path.insert(0, "/Users/tuwan/work/code/obsion/openWork")

# 导入Handler
import importlib.util

spec = importlib.util.spec_from_file_location(
    "bot", "/Users/tuwan/work/code/obsion/openWork/dingtalk_bot_d.py"
)
bot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bot)

# 创建Handler实例
handler = bot.DingTalkBotHandler()

# 测试消息列表
test_messages = ["你好", "介绍一下你的功能", "帮助", "什么是obsion", "最近7天的订单数据", "谢谢"]

print("=" * 60)
print("🤖 钉钉机器人D仔 - 本地Mock测试")
print("=" * 60)
print()

for i, msg in enumerate(test_messages, 1):
    print(f"测试 {i}/{len(test_messages)}")
    print(f"👤 用户: {msg}")
    print()
    response = handler.generate_response(msg)
    print("🤖 D仔回复:")
    print(response)
    print()
    print("-" * 60)
    print()

print("✅ 本地测试完成！机器人逻辑运行正常。")
print("⚠️  需要正确的钉钉凭证才能接收和发送真实消息。")
