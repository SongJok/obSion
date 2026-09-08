#!/bin/bash
# 发布钉钉机器人脚本

set -e

APP_ID="70753b24-2409-4adf-92eb-5b0d0980b3da"

echo "========================================="
echo "  发布钉钉机器人 - 点仔"
echo "========================================="
echo ""

# 1. 创建版本
echo "→ 创建新版本..."
VERSION_RESULT=$(dws dev version create \
  --unified-app-id "$APP_ID" \
  --version-desc "Obsion Alpha.1 - 企业智能助手" \
  --format json)

echo "$VERSION_RESULT"

VERSION=$(echo "$VERSION_RESULT" | python3 -c "
import sys, json
try:
    data = json.load(sys.stdin)
    if 'data' in data and 'version' in data['data']:
        print(data['data']['version'])
    else:
        print('ERROR')
except:
    print('ERROR')
")

if [ "$VERSION" = "ERROR" ]; then
    echo "✗ 创建版本失败"
    exit 1
fi

echo "✓ 版本创建成功: $VERSION"
echo ""

# 2. 检查是否需要审批
echo "→ 检查审批状态..."
APPROVAL_STATUS=$(dws dev version check-approval \
  --unified-app-id "$APP_ID" \
  --version "$VERSION" \
  --format json)

echo "$APPROVAL_STATUS"
echo ""

# 3. 发布版本
echo "→ 发布版本..."
PUBLISH_RESULT=$(dws dev version publish \
  --unified-app-id "$APP_ID" \
  --version "$VERSION" \
  --format json)

echo "$PUBLISH_RESULT"
echo ""

# 4. 检查状态
echo "→ 检查发布状态..."
STATUS=$(dws dev version status \
  --unified-app-id "$APP_ID" \
  --version "$VERSION" \
  --format json)

echo "$STATUS"
echo ""

echo "========================================="
echo "✓ 机器人发布完成！"
echo "========================================="
echo ""
echo "现在可以在群聊中使用机器人了："
echo "  @点仔 你好"
echo ""
