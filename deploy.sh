#!/bin/bash
# ==========================================
# fund-assistant 阿里云一键部署脚本
# 在服务器上执行:
#   curl -sSL https://raw.githubusercontent.com/allenshen-svg/fund-assistant/main/deploy.sh | bash
# 或:
#   git clone https://github.com/allenshen-svg/fund-assistant.git /opt/fund-assistant
#   cd /opt/fund-assistant && bash deploy.sh
# ==========================================

set -e

APP_DIR="/opt/fund-assistant"
SERVICE_NAME="fund-assistant"
VENV_DIR="$APP_DIR/.venv"
REPO_URL="https://github.com/allenshen-svg/fund-assistant.git"

RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

log()  { echo -e "${GREEN}[✓]${NC} $1"; }
warn() { echo -e "${YELLOW}[!]${NC} $1"; }
err()  { echo -e "${RED}[✗]${NC} $1"; }

echo ""
echo "=========================================="
echo "  📊 Fund-Assistant 阿里云一键部署"
echo "=========================================="
echo ""

# ---------- 0. 检查权限 ----------
if [ "$(id -u)" -eq 0 ]; then
    SUDO=""
    RUN_USER=${SUDO_USER:-root}
else
    SUDO="sudo"
    RUN_USER=$(whoami)
fi

# ---------- 1. 系统依赖 ----------
echo "[1/7] 安装系统依赖..."
if command -v apt-get &>/dev/null; then
    $SUDO apt-get update -qq 2>/dev/null
    $SUDO apt-get install -y -qq python3 python3-venv python3-pip git nginx curl >/dev/null 2>&1
    log "apt 依赖安装完成"
elif command -v yum &>/dev/null; then
    $SUDO yum install -y python3 python3-pip git nginx curl >/dev/null 2>&1
    log "yum 依赖安装完成"
elif command -v dnf &>/dev/null; then
    $SUDO dnf install -y python3 python3-pip git nginx curl >/dev/null 2>&1
    log "dnf 依赖安装完成"
else
    err "无法识别包管理器"
    exit 1
fi

# ---------- 2. 获取代码 ----------
echo ""
echo "[2/7] 获取代码..."
if [ -d "$APP_DIR/.git" ]; then
    cd "$APP_DIR"
    git pull --quiet
    log "代码已更新 (git pull)"
else
    $SUDO mkdir -p "$APP_DIR"
    $SUDO chown "$RUN_USER":"$RUN_USER" "$APP_DIR"
    git clone --quiet "$REPO_URL" "$APP_DIR"
    log "代码已克隆到 $APP_DIR"
fi
cd "$APP_DIR"

# ---------- 3. data 目录 ----------
mkdir -p "$APP_DIR/data"

# ---------- 4. Python 环境 ----------
echo ""
echo "[3/7] 配置 Python 环境..."
if [ ! -d "$VENV_DIR" ]; then
    python3 -m venv "$VENV_DIR"
fi
source "$VENV_DIR/bin/activate"
pip install --upgrade pip -q 2>/dev/null
pip install -r requirements.txt -q 2>/dev/null
log "Python 依赖安装完成"

# ---------- 5. 环境变量 ----------
echo ""
echo "[4/7] 配置环境变量..."
if [ ! -f "$APP_DIR/.env" ]; then
    cat > "$APP_DIR/.env" <<'ENVEOF'
# AI Key 已内置默认值，一般无需修改
# AI_API_KEY=your_key
# AI_PROVIDER=zhipu
# AI_MODEL=GLM-4-Flash
PORT=8000
COLLECT_INTERVAL=3600
ENVEOF
fi
log ".env 已生成（使用内置 API Key）"

# ---------- 6. Systemd 服务 ----------
echo ""
echo "[5/7] 配置 systemd 服务..."
$SUDO tee /etc/systemd/system/${SERVICE_NAME}.service > /dev/null <<EOF
[Unit]
Description=Fund-Assistant 舆情分析后端
After=network.target

[Service]
Type=simple
User=$RUN_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$VENV_DIR/bin/gunicorn --bind 127.0.0.1:8000 --workers 1 --threads 4 --timeout 120 server:app
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
EOF

$SUDO systemctl daemon-reload
$SUDO systemctl enable ${SERVICE_NAME} >/dev/null 2>&1
$SUDO systemctl restart ${SERVICE_NAME}
log "systemd 服务已启动并设为开机自启"

# ---------- 7. Nginx ----------
echo ""
echo "[6/7] 配置 Nginx..."

if [ -d /etc/nginx/sites-available ]; then
    NGINX_CONF="/etc/nginx/sites-available/${SERVICE_NAME}"
    NGINX_LINK="/etc/nginx/sites-enabled/${SERVICE_NAME}"
else
    NGINX_CONF="/etc/nginx/conf.d/${SERVICE_NAME}.conf"
    NGINX_LINK=""
fi

$SUDO tee "$NGINX_CONF" > /dev/null <<'NGINXEOF'
server {
    listen 80;
    server_name _;

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;
        proxy_connect_timeout 10s;

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
NGINXEOF

if [ -n "$NGINX_LINK" ]; then
    $SUDO ln -sf "$NGINX_CONF" "$NGINX_LINK"
    $SUDO rm -f /etc/nginx/sites-enabled/default
fi
$SUDO nginx -t 2>/dev/null && $SUDO systemctl reload nginx
log "Nginx 配置完成"

# ---------- 8. 防火墙 ----------
echo ""
echo "[7/7] 配置防火墙..."
if command -v ufw &>/dev/null; then
    $SUDO ufw allow 80/tcp >/dev/null 2>&1
    $SUDO ufw allow 22/tcp >/dev/null 2>&1
    log "ufw 已放行 80 端口"
elif command -v firewall-cmd &>/dev/null; then
    $SUDO firewall-cmd --permanent --add-port=80/tcp >/dev/null 2>&1
    $SUDO firewall-cmd --reload >/dev/null 2>&1
    log "firewalld 已放行 80 端口"
else
    warn "未检测到防火墙工具，跳过（阿里云安全组需手动放行）"
fi

# ---------- 验证 ----------
echo ""
echo "⏳ 等待服务启动 (3s)..."
sleep 3

if curl -s http://localhost/api/status 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['server']=='running'" 2>/dev/null; then
    log "服务验证通过！"
elif curl -s http://localhost:8000/api/status 2>/dev/null | python3 -c "import sys,json; d=json.load(sys.stdin); assert d['server']=='running'" 2>/dev/null; then
    log "服务验证通过（直连 8000 端口）"
else
    warn "服务可能还在启动中，15 秒后自动完成首次采集"
    warn "可用 sudo journalctl -u ${SERVICE_NAME} -f 查看日志"
fi

# ---------- 获取公网 IP ----------
PUBLIC_IP=$(curl -s --connect-timeout 5 ifconfig.me 2>/dev/null || \
            curl -s --connect-timeout 5 ipinfo.io/ip 2>/dev/null || \
            curl -s --connect-timeout 5 icanhazip.com 2>/dev/null || \
            echo "<你的公网IP>")

echo ""
echo -e "${GREEN}==========================================${NC}"
echo -e "${GREEN}  ✅ 部署完成！${NC}"
echo -e "${GREEN}==========================================${NC}"
echo ""
echo "  🌐 服务地址: http://${PUBLIC_IP}"
echo "  📋 验证命令: curl http://${PUBLIC_IP}/api/status"
echo ""
echo "  常用命令:"
echo "    查看日志: sudo journalctl -u ${SERVICE_NAME} -f"
echo "    重启服务: sudo systemctl restart ${SERVICE_NAME}"
echo "    更新代码: cd ${APP_DIR} && git pull && sudo systemctl restart ${SERVICE_NAME}"
echo ""
echo -e "${YELLOW}  ⚠️  还需你手动做 2 步:${NC}"
echo ""
echo "  1️⃣  阿里云控制台 → 安全组/防火墙 → 入方向放行 80 端口"
echo "  2️⃣  微信小程序「设置」页填入: http://${PUBLIC_IP}"
echo ""
echo "=========================================="
