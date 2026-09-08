#!/bin/bash
# 钉钉机器人D仔监控脚本

echo "🤖 钉钉机器人D仔状态监控"
echo "================================"
echo ""

# 检查进程
if ps aux | grep -q "[d]ingtalk_bot_d.py"; then
    PID=$(ps aux | grep "[d]ingtalk_bot_d.py" | awk '{print $2}')
    echo "✅ 机器人运行中"
    echo "   PID: $PID"
    
    # 检查运行时间
    START_TIME=$(ps -p $PID -o lstart= 2>/dev/null)
    echo "   启动时间: $START_TIME"
    
    # 检查内存使用
    MEM=$(ps -p $PID -o rss= 2>/dev/null)
    echo "   内存使用: $((MEM/1024)) MB"
    
    # 检查CPU使用
    CPU=$(ps -p $PID -o %cpu= 2>/dev/null)
    echo "   CPU使用: ${CPU}%"
else
    echo "❌ 机器人未运行"
    echo ""
    echo "启动命令："
    echo "  cd /Users/tuwan/work/code/obsion/openWork"
    echo "  python3 dingtalk_bot_d.py &"
fi

echo ""
echo "================================"
