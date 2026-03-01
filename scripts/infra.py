#!/usr/bin/env python3
"""
基础设施配置读取器
用法:
    from scripts.infra import infra, env

    # 读取 infra.json
    infra.domain        # 'xiaoniqiu.top'
    infra.server_host   # '47.83.165.131'
    infra.api_url       # 'https://xiaoniqiu.top/api'
    infra.appid         # 'wxdff3c1b4fad3158c'
    infra.raw           # 原始 dict

    # 读取 .env（自动加载）
    env('AI_API_KEY')           # 返回值或 None
    env('PORT', '8000')         # 带默认值
"""

import os, json

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_INFRA_PATH = os.path.join(_ROOT, 'infra.json')
_ENV_PATH = os.path.join(_ROOT, '.env')


# ==================== .env 加载 ====================
def _load_dotenv():
    """手动解析 .env 文件，注入 os.environ（不覆盖已有值）"""
    if not os.path.exists(_ENV_PATH):
        return
    with open(_ENV_PATH, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' not in line:
                continue
            key, _, val = line.partition('=')
            key = key.strip()
            val = val.strip()
            if not key:
                continue
            # 不覆盖已有环境变量
            if key not in os.environ:
                os.environ[key] = val

_load_dotenv()


def env(key, default=None):
    """读取环境变量（.env 已自动加载）"""
    return os.environ.get(key, default)


# ==================== infra.json 读取 ====================
class _Infra:
    """infra.json 的便捷访问器"""

    def __init__(self):
        self.raw = {}
        self._load()

    def _load(self):
        if os.path.exists(_INFRA_PATH):
            with open(_INFRA_PATH, 'r', encoding='utf-8') as f:
                self.raw = json.load(f)

    def reload(self):
        self._load()

    # ---- 服务器 ----
    @property
    def server_host(self):
        return self.raw.get('server', {}).get('host', '')

    @property
    def server_region(self):
        return self.raw.get('server', {}).get('region', '')

    @property
    def deploy_path(self):
        return self.raw.get('server', {}).get('deploy_path', '/opt/fund-assistant')

    @property
    def gunicorn_port(self):
        return self.raw.get('server', {}).get('services', {}).get('gunicorn', {}).get('port', 8080)

    # ---- 域名 ----
    @property
    def domain(self):
        return self.raw.get('domain', {}).get('name', '')

    @property
    def domain_url(self):
        return self.raw.get('domain', {}).get('urls', {}).get('https', '')

    @property
    def api_url(self):
        return self.raw.get('domain', {}).get('urls', {}).get('api', '')

    # ---- 微信小程序 ----
    @property
    def appid(self):
        return self.raw.get('wechat_miniprogram', {}).get('appid', '')

    # ---- GitHub ----
    @property
    def github_repo(self):
        return self.raw.get('github', {}).get('repo', '')

    @property
    def github_pages(self):
        return self.raw.get('github', {}).get('pages_base', '')

    # ---- AI ----
    @property
    def ai_provider(self):
        return env('AI_PROVIDER', self.raw.get('ai', {}).get('default_provider', 'zhipu'))

    @property
    def ai_model(self):
        return env('AI_MODEL', self.raw.get('ai', {}).get('default_model', 'GLM-4-Flash'))

    @property
    def ai_api_key(self):
        return env('AI_API_KEY', '')

    def ai_base_url(self, provider=None):
        p = provider or self.ai_provider
        return self.raw.get('ai', {}).get('providers', {}).get(p, {}).get('base', '')

    # ---- 快速打印 ----
    def summary(self):
        return f"""╔══════════════════════════════════════════════════╗
║  📋 Fund-Assistant 基础设施                      ║
╠══════════════════════════════════════════════════╣
║  🖥  服务器:  {self.server_host:<20} ({self.server_region})      ║
║  🌐 域名:    {self.domain:<35}  ║
║  🔗 API:     {self.api_url:<35}  ║
║  📱 AppID:   {self.appid:<35}  ║
║  🤖 AI:      {self.ai_provider}/{self.ai_model:<26}  ║
║  📦 GitHub:  {self.github_repo:<35}  ║
╚══════════════════════════════════════════════════╝"""

    def __repr__(self):
        return self.summary()


# 全局实例
infra = _Infra()


# 直接运行时打印摘要
if __name__ == '__main__':
    print(infra.summary())
    print()
    print('环境变量:')
    for k in ['SERVER_HOST', 'DOMAIN', 'DOMAIN_URL', 'AI_PROVIDER', 'AI_MODEL',
              'AI_API_KEY', 'PORT', 'GUNICORN_PORT', 'WECHAT_APPID']:
        v = env(k, '(未设置)')
        # 隐藏 API Key 中间部分
        if 'KEY' in k and len(v) > 10:
            v = v[:6] + '...' + v[-4:]
        print(f'  {k} = {v}')
