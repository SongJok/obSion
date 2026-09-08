#!/bin/bash
# Obsion 钉钉机器人启动脚本

set -e

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   Obsion 钉钉机器人 - 启动脚本${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""

# 切换到项目根目录
cd "$(dirname "$0")/.."
PROJECT_ROOT=$(pwd)

echo -e "${GREEN}✓${NC} 项目根目录: $PROJECT_ROOT"
echo ""

# 检查 dws 命令
if ! command -v dws &> /dev/null; then
    echo -e "${RED}✗ 错误: 未找到 dws 命令${NC}"
    echo "请先安装 dws CLI:"
    echo "  brew install dingtalk-workspace"
    exit 1
fi

echo -e "${GREEN}✓${NC} dws CLI 已安装"
echo ""

# 检查登录状态
echo -e "${YELLOW}→${NC} 检查钉钉登录状态..."
if ! dws contact user get-self &> /dev/null; then
    echo -e "${YELLOW}! 未登录，开始登录流程...${NC}"
    dws auth login
    echo ""
else
    echo -e "${GREEN}✓${NC} 已登录钉钉"
    # 显示当前用户信息
    USER_INFO=$(dws contact user get-self --format json 2>/dev/null | python3 -c "import sys, json; data=json.load(sys.stdin); print(data['result'][0]['orgEmployeeModel']['orgUserName'])" 2>/dev/null || echo "Unknown")
    echo -e "  当前用户: ${GREEN}$USER_INFO${NC}"
    echo ""
fi

# 获取应用列表
echo -e "${YELLOW}→${NC} 获取应用列表..."
APP_LIST=$(dws dev app list --format json)

# 查找 "点仔" 应用
APP_ID=$(echo "$APP_LIST" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for item in data.get('data', {}).get('items', []):
        if '点仔' in item.get('name', '') or 'Obsion' in item.get('desc', ''):
            print(item['unifiedAppId'])
            exit(0)
    print('')
except:
    print('')
")

if [ -z "$APP_ID" ]; then
    echo -e "${RED}✗ 错误: 未找到钉钉应用 '点仔'${NC}"
    echo ""
    echo "可用的应用列表:"
    echo "$APP_LIST" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    for item in data.get('data', {}).get('items', []):
        print(f\"  - {item['name']} (ID: {item['unifiedAppId']})\")
except:
    pass
"
    echo ""
    echo "请使用以下命令手动指定应用ID:"
    echo "  dws dev connect --agent-cmd \"python $PROJECT_ROOT/dingtalk_obsion_agent.py\" --unified-app-id <your-app-id>"
    exit 1
fi

echo -e "${GREEN}✓${NC} 找到应用: 点仔"
echo -e "  应用ID: ${BLUE}$APP_ID${NC}"
echo ""

# 检查代理脚本
AGENT_SCRIPT="$PROJECT_ROOT/dingtalk_obsion_agent.py"
if [ ! -f "$AGENT_SCRIPT" ]; then
    echo -e "${RED}✗ 错误: 代理脚本不存在${NC}"
    echo "  路径: $AGENT_SCRIPT"
    exit 1
fi

echo -e "${GREEN}✓${NC} 代理脚本就绪: dingtalk_obsion_agent.py"
echo ""

# 加载环境变量
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${YELLOW}→${NC} 加载环境变量..."
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
    echo -e "${GREEN}✓${NC} 环境变量已加载"
    echo ""
fi

# 显示配置信息
echo -e "${BLUE}========================================${NC}"
echo -e "${BLUE}   启动配置${NC}"
echo -e "${BLUE}========================================${NC}"
echo -e "  应用ID:       $APP_ID"
echo -e "  代理脚本:     $AGENT_SCRIPT"
echo -e "  API地址:      ${OBSION_API_URL:-http://localhost:58081}"
echo -e "  Web界面:      ${OBSION_WEB_URL:-http://localhost:53001}"
echo -e "  LLM模型:      ${LLM_MODEL:-关键词模式}"
echo ""

# 启动提示
echo -e "${BLUE}========================================${NC}"
echo -e "${GREEN}🚀 启动钉钉机器人...${NC}"
echo -e "${BLUE}========================================${NC}"
echo ""
echo -e "${YELLOW}提示:${NC}"
echo -e "  1. 将机器人添加到群聊"
echo -e "  2. 使用 @点仔 <你的问题> 进行交互"
echo -e "  3. 按 Ctrl+C 停止机器人"
echo ""
echo -e "${BLUE}========================================${NC}"
echo ""

# 启动 dws connect
exec dws dev connect \
    --agent-cmd "python3 $AGENT_SCRIPT" \
    --unified-app-id "$APP_ID"
