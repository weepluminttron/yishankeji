# AI 获客系统（通用版）

一套开箱即用的 B2B 获客工具：自动找客户线索、AI 获客向导、客户管理（轻量 CRM）、AI 评分与主动触达，一套代码即可私有部署，不限行业。

## 核心功能

1. **AI 获客向导**
   输入一句话描述你的业务（不限行业）→ 选择“你的角色”（贸易商/分销商/代理商/生产厂家/外贸等）→ 选择客户所在地区 → 直接开始找客户，不需要自己拼关键词。

2. **多渠道自动找客户**
   - 搜索引擎：Bing / 360 / 百度 / 搜狗，支持 SerpAPI、博查等商业搜索源；
   - 地图 POI：高德地图自动检索多个城市的弱电工程商、系统集成商、通信工程公司；
   - 行业网站、展会名录、招投标平台、企业黄页；
   - 自动去噪、去重，提取公司名、电话、邮箱、地址。

3. **AI 线索评分**
   每条线索按“匹配度 + 实力”双维度打 S/A/B/C 级，可调用 DeepSeek 等大模型给出评分理由和下一步动作建议（先电话确认 / 发样品 / 报价单等）。

4. **客户资料管理（轻量 CRM）**
   客户列表、搜索筛选、跟进状态、跟进备注、下次提醒、批量导入导出、数据清洗去重、获客分析漏斗。

5. **主动触达**
   内置邮件/短信模板，SMTP 群发自动个性化（公司名、联系人、地区）；AI 一键生成首触邮件、短信、开场话术。

6. **获客落地页 / 表单**
   独立公网落地页，访客填表后自动成为客户线索，内置反垃圾。

## 运行环境

- 本机 Windows / macOS，或一台云服务器（推荐 2核4G 及以上 Linux）
- Python 3.10+（也可用 Docker，无需安装 Python）
- 数据存 SQLite 单文件，无需安装数据库

## 快速开始（30 秒看效果）

1. 把 `.env.example` 复制为 `.env`，按注释填写密钥（至少填后台密码，其余可后补）；
2. 启动服务（任选一种）：
   - Windows：双击 `启动工具.bat`；
   - Linux：`bash start.sh`；
   - Docker：`docker compose up -d --build`；
3. 浏览器打开 `http://你的服务器IP:8017`，输入后台密码登录。

详细步骤见 [部署文档 DEPLOY.md](DEPLOY.md)，功能使用见 [买家版说明书 USER_GUIDE.md](USER_GUIDE.md)。

## 目录结构

```
├── server.py              # 主程序（Flask 风格单文件 Web 服务）
├── core/                  # 核心逻辑：获客引擎、买家发现、评分、邮件、地图等
├── static/                # 前端页面
├── scripts/               # 辅助脚本（命令行获客、测试等）
├── data/                  # 运行数据（首次启动自动创建，含线索库 settings 等）
├── examples/              # 演示数据（虚构客户，供熟悉系统）
├── .env.example           # 配置文件模板（复制为 .env 后填写）
├── Dockerfile / docker-compose.yml
├── DEPLOY.md / USER_GUIDE.md
```

## 配置说明（.env）

所有密钥集中在一个文件 `.env` 里，改完重启服务即生效：

| 配置项 | 说明 | 必填 |
|---|---|---|
| ACCESS_PASSWORD | 后台登录密码 | ✅ |
| OPENAI_API_KEY / OPENAI_MODEL / OPENAI_API_BASE | AI 接口（DeepSeek 等） | 推荐 |
| SEARCH_PROVIDER / SERPAPI_API_KEY / BOCHA_API_KEY | 搜索源 | 推荐 |
| MAP_API_KEY / MAP_PROVIDER | 高德地图（找工程商） | 推荐 |
| PROXY_ENABLED / PROXY_URL / PROXY_API_URL | 代理（被限流时启用） | 可选 |
| QCC_APP_KEY / QCC_SECRET_KEY / TYC_TOKEN | 企查查/天眼查 | 可选 |
| SMTP_HOST / SMTP_USER / SMTP_PASSWORD | 邮件触达 | 可选 |

## 法律与合规声明

- 本系统仅采集互联网**公开可见**的信息，请勿用于获取非公开、需登录或个人隐私数据；
- 请遵守目标网站的 robots 协议并控制请求频率，不得破解验证码、绕过防护；
- 本项目内置的客户线索仅用于**合法商业分析**，请勿用于骚扰、恶意营销或侵犯他人权益；
- `examples/` 中的演示数据均为**虚构**，仅供熟悉系统，请勿当作真实客户使用；
- 源码按 LICENSE 授权使用，仅限购买方使用，禁止二次转卖。
