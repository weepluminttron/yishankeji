# 光纤行业获客助手

专为光纤行业（光缆、光纤收发器、熔接施工、FTTH、机房改造、弱电工程等）设计的获客工具，支持在**本机**或**腾讯云服务器**上运行，包含四大能力：

1. **自动找客户线索**
   - 网页采集：粘贴企业黄页 / 目录页网址，自动提取公司名与电话；
   - 定时自动采集：配置网址列表和间隔，后台定时抓取、自动去重入库；
   - Excel / CSV 批量导入（自动去重）、手动录入。
2. **获客落地页 / 表单**
   - 独立公网落地页 `http://服务器IP:8017/lp`，访客填写姓名、电话、需求后自动成为客户线索；
   - 页面文案、咨询电话、按钮文字都可在“设置”里自定义；
   - 内置反垃圾（蜜罐字段 + 频率限制）。
3. **客户资料管理（轻量 CRM）**
   - 客户类型、跟进状态、标签、跟进备注、下次跟进提醒；
   - 搜索、筛选、批量操作、一键导出 Excel / CSV。
4. **主动触达工具**
   - 内置 5 套光纤行业邮件模板 + 3 套短信模板；
   - SMTP 邮件群发（自动个性化：{{公司}} {{联系人}} {{地区}}…）；
   - AI 智能生成营销文案（可选配置 OpenAI Key）；
   - 短信名单一键复制 / 导出。

## 本机运行

双击 **`启动工具.bat`**，浏览器打开 `http://127.0.0.1:8017`。

所有数据保存在 `data/app.db`，备份时复制 `data` 文件夹即可。

## 部署到腾讯云服务器（Linux）

### 1. 安装运行环境（Ubuntu / Debian 示例）

```bash
sudo apt update
sudo apt install -y git python3 python3-venv
```

### 2. 拉取代码并安装依赖

```bash
sudo mkdir -p /opt/yishankeji
sudo chown $USER /opt/yishankeji
cd /opt/yishankeji
git clone https://github.com/weepluminttron/yishankeji.git .
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
```

### 3. 设置访问密码

```bash
cp server.env.example server.env
nano server.env   # 把 ACCESS_PASSWORD 改成你的强密码，HOST 保持 0.0.0.0
```

### 4. 注册为系统服务（开机自启 + 崩溃自动重启）

```bash
sudo cp deploy/yishankeji.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable yishankeji
sudo systemctl start yishankeji
sudo systemctl status yishankeji
```

### 5. 打开云服务器防火墙端口

在腾讯云控制台（服务器 → 防火墙 / 安全组）放行 **TCP 8017**，然后访问：

- 管理后台：`http://服务器公网IP:8017`（需要访问密码）
- 获客落地页：`http://服务器公网IP:8017/lp`（客户可公开访问并提交表单）

### 更新代码

```bash
cd /opt/yishankeji
git pull
sudo systemctl restart yishankeji
```

## 邮件配置（SMTP）

发信邮箱需要支持 SMTP：

- **QQ 邮箱**：设置 → 账户 → 开启“POP3/SMTP 服务”并生成授权码；服务器 `smtp.qq.com`，端口 465（SSL）。
- **163 邮箱**：设置 → 客户端授权密码；服务器 `smtp.163.com`，端口 465（SSL）。
- **企业邮 / 阿里邮箱**：一般也是 `smtp.公司域名` + 465/587。

在“设置”里填写后点“保存”，再点“发送测试邮件”确认。

## 短信说明

短信需通过运营商短信网关（如阿里云短信）发送，需要营业执照和签名审核。当前版本提供短信模板、号码一键复制和导出，方便粘贴到手机短信或第三方群发平台。

## 采集合规提示

请只采集你有权使用的公开信息，遵守网站条款和相关法律法规。部分网站有验证码、登录墙或反爬措施，采集不到时建议改用 Excel 导入。

## 常见问题

- **页面打不开**：检查腾讯云防火墙/安全组是否放行 8017；检查服务状态 `sudo systemctl status yishankeji`。
- **忘记访问密码**：编辑 `server.env` 重新设置，重启服务即可。
- **落地页提交失败**：确认“设置 → 获客落地页”状态为开启。

## 技术说明

- Python 标准库（HTTP 服务）+ SQLite，无第三方依赖（Excel 用 openpyxl、网页解析用 lxml，随附带好）。
- 默认只在本机 `127.0.0.1:8017` 提供服务，其他设备无法访问。
