# klqzbot

`klqzbot` 是一个独立的 Telegram 群消息同步工具仓库。

当前主架构是：

1. 用 `session` 账号加入并监听 A 群
2. 用 `BOT_TOKEN` 对应的机器人把消息发送到 B 群
3. 通过“重发”而不是“转发”来隐藏来源

## 当前能力

- 实时监听 A 群新消息
- 抓取文案、媒体、按钮
- 用机器人身份发送到 B 群
- 不显示转发来源
- 自动从 `./session` 目录发现 `.session` 文件

## 环境要求

- Python 3.12+
- 一个已授权的 Telegram 用户号 `.session`
- 一个可发消息到 B 群的 bot
- `API_ID` / `API_HASH` / `BOT_TOKEN`

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## 环境变量

通过 `.env` 配置：

```env
API_ID=2040
API_HASH=b18441a1ff607e10a989891a5462e627
BOT_TOKEN=123456:ABCDEF...
```

## session 目录

把监听账号的 `.session` 文件放到项目根目录下的 `session/`：

```text
klqzbot/
  session/
    my_listener.session
```

如果 `session/` 里只有一个 `.session` 文件，程序会自动拿它来监听 A 群。

也可以手动指定：

```bash
python -m klqzbot mirror ^
  --session "C:\path\to\my_listener.session" ^
  --source "https://t.me/A群" ^
  --target "https://t.me/B群"
```

## 用法

### 标准模式

```bash
python -m klqzbot mirror ^
  --source "https://t.me/A群" ^
  --target "https://t.me/B群"
```

这条命令会：

- 自动读取 `./session/*.session`
- 用该 session 账号监听 A 群
- 用 `.env` 里的 `BOT_TOKEN` 把消息发到 B 群

### 指定 session 目录

```bash
python -m klqzbot mirror ^
  --session-dir "C:\my-sessions" ^
  --source "https://t.me/A群" ^
  --target "https://t.me/B群"
```

## 前提

- 监听账号必须已经加入 A 群
- bot 必须已经加入 B 群
- bot 在 B 群里要有发消息权限

## 还没做

- 同步消息编辑
- 同步消息删除
- 多个源群同步到多个目标群
- 本地规则过滤
