# 网页端直达部署

这个项目不是纯静态网页。GitHub Pages 只能展示 `docs/index.html` 推广页，不能运行登录、SQLite 数据库、OCR、行情刷新和基金季报抓取。要让小白点开链接直接使用，需要部署到能运行 Python 后端的云服务。

## 一键部署到 Render

点击下面的按钮，登录 Render 后按页面提示创建服务：

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/atlantisjoin-maker/stock)

部署成功后，Render 会给你一个类似下面的 HTTPS 地址：

```text
https://astock-terminal-xxxx.onrender.com
```

把这个地址发给用户即可直接打开网页端。第一次打开时创建账户；第一个账户是管理员，后续用户可以自行注册，默认看不到你的持仓数据。

## 必须保留的数据目录

本项目把账户、会话、持仓、观察池和提醒保存在 SQLite 数据库里。云端部署时必须让 `ASTOCK_HOME` 指向持久化目录：

```text
ASTOCK_HOME=/data
```

`render.yaml` 已经配置了 `/data` 持久化磁盘。没有持久化磁盘时，服务重启或重新部署后账户和持仓可能丢失，只适合临时演示。

## 新手操作顺序

1. 打开一键部署按钮。
2. 登录 Render，授权使用公开仓库。
3. 确认服务名、地区和磁盘配置。
4. 等待构建完成，打开 Render 给出的 `onrender.com` 地址。
5. 创建第一个账户，保存好密码。
6. 把这个网址设置成浏览器书签，或生成短链接用于推广。

## 本地 Docker 验证

已经安装 Docker 的机器可以这样运行：

```bash
docker build -t astock-terminal .
docker run --rm -p 8765:8765 -v astock-data:/data astock-terminal
```

浏览器访问：

```text
http://127.0.0.1:8765
```

## 刷新频率

默认行情刷新周期是 10 秒；新闻后台刷新 120 秒；市场热度信号 300 秒；估值代理 3600 秒。实际速度会受外部数据源限流、云服务网络和基金公告披露节奏影响。

## 隐私边界

源码仓库是公开的，但个人数据不进仓库。每个部署实例都有自己的 `/data/terminal.db`；不同人如果各自部署，就是各自的数据。多人共用同一个部署网址时，账户隔离生效：其他用户注册后持仓为空，不能读取你的持仓页。
