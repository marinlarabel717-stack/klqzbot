# klqzbot

`klqzbot` 是一个独立的 Telegram 群消息同步工具仓库。

当前主架构是：

1. 用监听账号加入并监听 A 群
2. 用 `BOT_TOKEN` 对应的机器人把消息发送到 B 群
3. 通过“重发”而不是“转发”来隐藏来源

## 当前能力

- 实时监听 A 群新消息
- 抓取文案、媒体
- 用机器人身份发送到 B 群
- 不显示转发来源
- 自动从 `./session` 目录发现 `.session` 文件
- 支持监听账号从 `.env` 的手机号/验证码初始化 session
- A 群 / B 群引用可直接写在 `.env`
- 仅允许 `.env` 白名单账号在消息里声明跳转按钮

## 环境要求

- Python 3.12+
- 一个可用于监听 A 群的 Telegram 用户号
- 一个可发消息到 B 群的 bot
- `API_ID` / `API_HASH` / `BOT_TOKEN`

## 安装

```bash
git clone https://github.com/marinlarabel717-stack/klqzbot.git
cd klqzbot
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

如果你习惯传统脚本启动方式，仓库根目录也支持：

```bash
python bot.py mirror
```

## 环境变量

先把 `.env.example` 复制成 `.env`，再填值：

```env
API_ID=2040
API_HASH=b18441a1ff607e10a989891a5462e627
BOT_TOKEN=123456:ABCDEF...
SOURCE_CHAT=https://t.me/source_group
TARGET_CHAT=https://t.me/target_group
LISTENER_SESSION=session/listener.session
LISTENER_PHONE=+8613800000000
LISTENER_CODE=
LISTENER_PASSWORD=
BUTTON_ADMIN_IDS=123456789,987654321
```

说明：

- `SOURCE_CHAT`：A 群引用
- `TARGET_CHAT`：B 群引用
- `LISTENER_SESSION`：监听账号本地 session 保存路径
- `LISTENER_PHONE`：监听账号手机号
- `LISTENER_CODE`：第一次登录时的短信/接码验证码，登录成功后可清空
- `LISTENER_PASSWORD`：监听账号若开启两步验证，在这里填写密码
- `BUTTON_ADMIN_IDS`：允许配置跳转按钮的 Telegram 用户 ID，多个用英文逗号分隔
- 群引用支持 `@username`、`https://t.me/...`、`https://t.me/+inviteHash`

## 监听 session

有两种用法。

### 1. 直接放现成 `.session`

```text
klqzbot/
  .env
  session/
    my_listener.session
```

如果 `session/` 里只有一个 `.session` 文件，程序会自动拿它来监听 A 群。

也可以手动指定：

```bash
python bot.py mirror --session "C:\path\to\my_listener.session"
```

### 2. 用手机号/接码初始化 session

第一次可以先登录生成监听 session：

```bash
python bot.py login
```

这条命令会：

- 读取 `.env` 里的 `LISTENER_SESSION`
- 读取 `LISTENER_PHONE`
- 优先使用 `LISTENER_CODE` / `LISTENER_PASSWORD`
- 如果没填验证码或两步密码，且当前终端可交互，会直接提示输入

登录成功后，会生成 `LISTENER_SESSION` 对应的 `.session` 文件，后续 `mirror` 直接复用。

## 用法

### 最简启动

当 `.env` 已配置好 `SOURCE_CHAT` / `TARGET_CHAT` 后：

```bash
python bot.py mirror
```

这条命令会：

- 优先复用 `LISTENER_SESSION` 或 `./session/*.session`
- 如果没有可用 session，会尝试用 `LISTENER_PHONE` 登录生成一个
- 用该监听账号监控 A 群
- 用 `.env` 里的 `BOT_TOKEN` 把消息发到 B 群

## 管理员按钮格式

现在不再复制 A 群原消息自带的按钮。

只有 `BUTTON_ADMIN_IDS` 里配置过的账号发的消息，才会被解析按钮声明；其他成员发同样格式，也只会按普通文本转发。

格式是一行一个按钮：

```text
按钮文字｜跳转的链接
```

例如：

```text
这是正文

立即购买｜https://example.com/buy
联系客服｜https://t.me/example_support
```

转发到 B 群后：

- `这是正文` 会作为消息正文
- 每一行 `按钮文字｜链接` 会生成一个独立按钮
- 支持半角 `|` 和全角 `｜`
- 链接目前只接受 `http://` 或 `https://`

### 覆盖 `.env` 配置

```bash
python bot.py mirror ^
  --source "https://t.me/source_group" ^
  --target "https://t.me/target_group"
```

### 指定 session 目录

```bash
python bot.py mirror ^
  --session-dir "C:\my-sessions"
```

### 临时覆盖监听登录参数

```bash
python bot.py login ^
  --phone "+8613800000000" ^
  --code "12345"
```

## 前提

- 监听账号必须已经加入 A 群
- bot 必须已经加入 B 群
- bot 在 B 群里要有发消息权限
- 如果使用 `LISTENER_CODE`，它是一次性验证码，登录成功后可以从 `.env` 删掉

## 还没做

- 同步消息编辑
- 同步消息删除
- 多个源群同步到多个目标群
- 本地规则过滤
