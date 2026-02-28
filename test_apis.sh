#!/bin/bash
echo "=== Testing AI API connectivity ==="

echo -n "1. Groq (免费,海外): "
curl -s --connect-timeout 5 -w "%{http_code}" -o /dev/null https://api.groq.com/openai/v1/chat/completions
echo ""

echo -n "2. 阿里百炼 (免费额度): "
curl -s --connect-timeout 5 -w "%{http_code}" -o /dev/null https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions
echo ""

echo -n "3. 智谱GLM (免费额度): "
curl -s --connect-timeout 5 -w "%{http_code}" -o /dev/null https://open.bigmodel.cn/api/paas/v4/chat/completions
echo ""

echo -n "4. Kimi/月之暗面 (免费额度): "
curl -s --connect-timeout 5 -w "%{http_code}" -o /dev/null https://api.moonshot.cn/v1/chat/completions
echo ""

echo -n "5. 硅基流动: "
curl -s --connect-timeout 5 -w "%{http_code}" -o /dev/null https://api.siliconflow.cn/v1/chat/completions
echo ""

echo -n "6. DeepSeek: "
curl -s --connect-timeout 5 -w "%{http_code}" -o /dev/null https://api.deepseek.com/v1/chat/completions
echo ""

echo -n "7. 零一万物/Yi: "
curl -s --connect-timeout 5 -w "%{http_code}" -o /dev/null https://api.lingyiwanwu.com/v1/chat/completions
echo ""

echo "=== Done (000=连接失败, 4xx=网络通但需认证) ==="
