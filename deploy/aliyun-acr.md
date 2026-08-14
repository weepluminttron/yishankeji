# 阿里云容器镜像（ACR）部署指南

本项目已按你的阿里云 ACR 实例配置好镜像仓库：

```
crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111
```

## 一、本地（或 ECS）构建镜像

```bash
docker build -t crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:latest .
```

## 二、登录 ACR（需要访问凭证密码）

```bash
docker login --username=nick5937453517 crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com
```

> 密码是开通容器镜像服务时设置的访问凭证密码，不是阿里云登录密码。
> 忘记可到 阿里云控制台 → 容器镜像服务 → 访问凭证 里重置。

## 三、推送到仓库

```bash
docker push crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:latest
```

生产环境建议用版本号代替 `latest`，例如：

```bash
docker tag crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:latest crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:v1.0
docker push crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:v1.0
```

## 四、在阿里云 ECS 上部署运行

```bash
docker pull crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:latest

mkdir -p /data/yishankeji

docker run -d --name yishankeji --restart unless-stopped \
  -p 8017:8017 \
  -v /data/yishankeji:/app/data \
  -e ACCESS_PASSWORD=123456lfs \
  crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:latest
```

- 后台地址：`http://服务器公网IP:8017`
- 后台密码：`123456lfs`（可改成你自己要的密码）
- 数据库、搜索缓存、线索数据都保存在宿主机 `/data/yishankeji`，升级镜像不会丢数据。

## 五、查看日志 / 停止 / 更新

```bash
# 看运行日志
docker logs -f yishankeji

# 停止并删除旧容器
docker stop yishankeji
docker rm yishankeji

# 拉新镜像后重新运行（数据卷不变，数据不丢）
docker pull crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:latest
docker run -d --name yishankeji --restart unless-stopped \
  -p 8017:8017 \
  -v /data/yishankeji:/app/data \
  -e ACCESS_PASSWORD=123456lfs \
  crpi-jo8sc8us8vqvvoq7.cn-hangzhou.personal.cr.aliyuncs.com/get1/111:latest
```

## 六、安全组

阿里云 ECS 安全组需要放行 TCP 8017 端口，否则后台打不开。

## 七、注意：获客引擎需要可用的搜索额度

镜像里的搜索接口目前配置为 **SerpAPI**。如果你的 SerpAPI 账户当月额度已用完（返回 429），
引擎会找不到客户。部署后请到“设置 → 搜索接口”确认：

- SerpAPI 有可用额度；或
- 换成博查 AI 搜索（open.bochaai.com 申请 64 位密钥）；或
- 使用免费源（360/搜狗/Bing，国内机房可能被限流，建议配合动态代理 IP）。
