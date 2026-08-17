# 部署文档（DEPLOY.md）

本文档教你把“AI 获客系统”跑起来，三种方式任选其一：

- **方式一：Windows 本机运行**（最快体验）
- **方式二：Linux 云服务器运行**（推荐正式使用）
- **方式三：Docker 一键部署**（最省心）

---

## 0. 准备材料

开始前先准备好以下内容（没有的可以先跳过，系统也能跑，只是部分功能用不了）：

| 材料 | 用途 | 哪里申请 |
|---|---|---|
| AI 密钥（DeepSeek 等） | AI 方案生成、线索评分 | platform.deepseek.com，充值少量即可 |
| 高德地图 Key | 地图渠道找工程商/集成商 | lbs.amap.com 免费申请（Web服务 Key） |
| 搜索源密钥 | 搜索更稳定 | SerpAPI（serpapi.com）或博查（open.bochaai.com），二选一即可 |
| 代理（可选） | 免费搜索被限流时换 IP | 有代理、快代理等，可后补 |
| 企业邮箱 SMTP（可选） | 自动发开发信 | 阿里云/腾讯企业邮箱 |

---

## 1. 配置文件 `.env`

把交付包里的 `.env.example` 复制一份，命名为 `.env`，然后用记事本/VS Code 打开填写：

```
# 最低配置：只填这个也能启动后台
ACCESS_PASSWORD=你的后台登录密码

# 推荐配置（AI 能力）
OPENAI_API_BASE=https://api.deepseek.com/v1
OPENAI_MODEL=deepseek-chat
OPENAI_API_KEY=sk-你的DeepSeek密钥

# 推荐配置（地图找客户）
MAP_API_KEY=你的高德Key
MAP_PROVIDER=amap
```

> 注意：密钥不要带空格、不要加引号；`#` 开头是注释。所有密钥都只放在 `.env` 里，代码里没有写死任何密钥。

---

## 2. 方式一：Windows 本机运行

1. 安装 Python 3.10 或更高版本（安装时勾选 “Add Python to PATH”）；
2. 双击 `启动工具.bat`，或打开命令行执行：
   ```
   pip install -r requirements.txt
   python server.py
   ```
3. 看到 `listening on 0.0.0.0:8017` 表示启动成功；
4. 浏览器打开 `http://127.0.0.1:8017`，输入后台密码登录。

> 本机运行仅供自己测试/自用。要让别人能访问，请用方式二或方式三部署到云服务器。

---

## 3. 方式二：Linux 云服务器（Ubuntu 示例）

### 3.1 准备服务器

买一台云服务器（腾讯云/阿里云等），建议 2核4G、Ubuntu 22.04 或 24.04，系统盘 40G 以上。记下公网 IP 和 root/ubuntu 密码。

### 3.2 上传代码

用你电脑上的 WinSCP / Xftp / FinalShell 等工具，把整个交付包上传到服务器 `/data/yishankeji`（目录名可以自己定）。

### 3.3 安装 Python 环境

在服务器终端执行：

```bash
cd /data/yishankeji
sudo apt update
sudo apt install -y python3 python3-venv python3-pip
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 3.4 配置 `.env`

```bash
cp .env.example .env
nano .env    # 填写密钥后 Ctrl+O 保存，Ctrl+X 退出
```

### 3.5 开机自启（systemd）

执行：

```bash
sudo cp deploy/yishankeji.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now yishankeji
sudo systemctl status yishankeji    # 看到 active (running) 即成功
```

### 3.6 开放防火墙端口

```bash
sudo ufw allow 8017/tcp
```

之后浏览器打开 `http://服务器IP:8017` 即可访问。

---

## 4. 方式三：Docker 一键部署（推荐）

服务器装好 Docker 后：

```bash
cd /data/yishankeji
cp .env.example .env        # 填写 .env
nano .env
docker compose up -d --build
docker compose ps           # 看到 yishankeji 状态 Up 即成功
```

停止/启动：

```bash
docker compose down         # 停止
docker compose up -d        # 再启动
```

升级代码后重建：

```bash
docker compose up -d --build --force-recreate
```

---

## 5. 常见问题

**Q1：端口被占用？**
修改 `.env` 里的 `PORT=8017` 为其他端口（如 8018），重启服务；Docker 方式还需同步改 docker-compose.yml 里的映射端口。

**Q2：公网访问不了？**
检查云服务器安全组是否放行 8017 端口（腾讯云控制台 → 安全组 → 添加 TCP 8017），服务器防火墙 ufw 也要放行。

**Q3：后台一直提示密钥格式不对？**
密钥不要带空格、换行、引号，也不要粘贴“sk-”以外的说明文字。SerpAPI 密钥是 64 位十六进制。

**Q4：数据存在哪里？怎么备份？**
全部数据在 `data/` 目录，备份时整个目录复制走即可。恢复时把备份放回 `data/` 再重启。

**Q5：搜索总是被限流？**
免费搜索源（360/Bing 等）容易被限流。建议：① 配置 SerpAPI 或博查商业源；② 启用代理（.env 里 PROXY_ENABLED=1，填 PROXY_URL 或 PROXY_API_URL）；③ 降低“检索式数量”；④ 等 10 分钟限流自动恢复。

**Q6：不想让别人直接用 8017 裸奔？**
建议反代加 HTTPS（Nginx + 免费证书），或至少把后台密码改强并只允许自己的 IP 访问（后台设置里有“记住本机IP自动登录”可关闭）。
