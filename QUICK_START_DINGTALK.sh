#!/bin/bash
# 钉钉机器人D仔快速启动脚本

set -e

echo "🤖 启动钉钉机器人D仔..."
echo ""

# 设置环境变量
: "${OBSION_DINGTALK_APP_KEY:?请通过本地环境提供 OBSION_DINGTALK_APP_KEY}"
export OBSION_DINGTALK_APP_KEY
: "${OBSION_DINGTALK_APP_SECRET:?请通过本地环境提供 OBSION_DINGTALK_APP_SECRET}"
export OBSION_DINGTALK_APP_SECRET
export OBSION_URL=http://localhost:58081
: "${OBSION_TOKEN:?请通过本地环境提供 OBSION_TOKEN}"
export OBSION_TOKEN

echo "✅ 环境变量已设置"
echo ""

# 检查健康状态
echo "🔍 检查钉钉连接..."
uv run obsion-im --channel dingtalk --deliver dingtalk-http health
echo ""

# 启动Webhook服务器
echo "🚀 启动Webhook服务器 (监听 0.0.0.0:8787)..."
echo ""
echo "📝 配置钉钉开放平台Webhook地址为:"
echo "   http://your-public-ip:8787/api/v1/im/dingtalk/webhook"
echo ""
echo "💡 本地测试可使用 ngrok: ngrok http 8787"
echo ""

uv run obsion-im --channel dingtalk serve --listen 0.0.0.0:8787
