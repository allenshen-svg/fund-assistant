#!/bin/bash
# ==========================================
# fund-assistant 云服务器一键部署脚本
# 支持: Ubuntu 20.04+ / Debian 11+ / CentOS 8+
# ==========================================

set -e

APP_DIR="/opt/fund-assistant"
SERVICE_NAME="fund-assistant"
PYTHON="python3"
VENV_DIR="$APP_DIR/.venv"

echo "=========================================="
echo "  📊 Fund-Assistant 云服务器部署"
echo "=========================================="

# ---------- 1. 系统依赖 ----------
echo ""
echo "[1/6] 安装系统依赖..."
if command -v apt-get &>/dev/null; then
    sudo apt-get update -qq
    sudo apt-get install -y -qq python3 python3-venv python3-pip git nginx
elif command -v yum &>/dev/null; then
    sudo yum install -y python3 python3-pip git nginx
elif command -v dnf &>/dev/null; then
    sudo dnf install -y python3 python3-pip git nginx
else
    echo "⚠️  无法识别包管理器，请手动安装 python3, git, nginx"
fi

# ---------- 2. 获取代码 ----------
echo ""
echo "[2/6] 部署代码到 $APP_DIR ..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git pull
else
    sudo mkdir -p "$APP_DIR"
    sudo chown $(whoami):$(whoami) "$APP_DIR"
    # 如果从本地上传，替代 git clone:
    # scp -r ./ user@server:/opt/fund-assistant/
    echo "请将代码上传到 $APP_DIR，或执行:"
    echo "  git clone https://github.com/allenshen-svg/fund-assistant.git $APP_DIR"
    if [ ! -f "$APP_DIR/server.py" ]; then
        echo "❌ 未检测到 server.py，请先上传代码"
        exit 1
    fi
fi

cd "$APP_DIR"

# ---------- 3. Python 虚拟环境 ----------
echo ""
echo "[3/6] 创建 Python 虚拟环境..."
if [ ! -d "$VENV_DIR" ]; then
    $PYTHON -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q
pip install -r requirements.txt -q

# ---------- 4. 环境变量 ----------
echo ""
echo "[4/6] 配置环境变量..."
if [ ! -f "$APP_DIR/.env" ]; then
    cp "$APP_DIR/.env.example" "$APP_DIR/.env"
    echo "⚠️  请编辑 $APP_DIR/.env 填入你的 AI_API_KEY"
    echo "   nano $APP_DIR/.env"
fi

# ---------- 5. Systemd 服务 ----------
echo ""
echo "[5/6] 配置 systemd 服务..."
sudo tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Fund-Assistant 舆情分析服务
After=network.target

[Service]
Type=simple
User=$(whoami)
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 4 --timeout 120 server:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable ${SERVICE_NAME}
sudo systemctl restart ${SERVICE_NAME}

echo "  ✅ 服务已启动"

# ---------- 6. Nginx 反向代理 ----------
echo ""
echo "[6/6] 配置 Nginx 反向代理..."
sudo tee /etc/nginx/sites-available/${SERVICE_NAME} > /dev/null <<'EOF'
server {
    listen 80;
    server_name _;   # 替换为你的域名或公网IP

    # 安全: 仅允许 API 路由，不暴露静态文件
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;

        # CORS - 微信小程序需要
        add_header Access-Control-Allow-Origin '*' always;
        add_header Access-Control-Allow-Methods 'GET, POST, OPTIONS' always;
        add_header Access-Control-Allow-Headers 'Content-Type' always;
        if ($request_method = OPTIONS) {
            return 204;
        }
    }

    location / {
        return 404;
    }
}
EOF

# 启用站点
if [ -d /etc/nginx/sites-enabled ]; then
    sudo ln -sf /etc/nginx/sites-available/${SERVICE_NAME} /etc/nginx/sites-enabled/
    sudo rm -f /etc/nginx/sites-enabled/default
fi
sudo nginx -t && sudo systemctl reload nginx

echo ""
echo "=========================================="
echo "  ✅ 部署完成！"
echo "=========================================="
echo ""
echo "  服务地址: http://$(curl -s ifconfig.me 2>/dev/null || echo '<你的公网IP>'):80"
echo ""
echo "  验证命令:"
echo "    curl http://localhost/api/status"
echo ""
echo "  日志查看:"
echo "    sudo journalctl -u ${SERVICE_NAME} -f"
echo ""
echo "  ⚠️ 记得:"
echo "    1. 编辑 .env 填入 AI_API_KEY"
echo "    2. 开放云服务器安全组的 80 端口"
echo "    3. 在小程序设置中填入服务器地址 http://<公网IP>"
echo "=========================================="
