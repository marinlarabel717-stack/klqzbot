# klqzbot

`klqzbot` 是一个独立的 Telegram 群组克隆工具仓库。

当前版本先提供一个可运行的 Python CLI 骨架，目标是围绕下面这条主流程继续完善：

1. 连接 Telegram 账号会话
2. 读取源群成员
3. 过滤不可邀请对象
4. 按节奏邀请进目标群
5. 输出执行结果

## 当前能力

- 支持 `@username`、`https://t.me/...`、`https://t.me/+inviteHash` 形式的群组引用
- 支持读取源群成员
- 支持把成员邀请进目标群
- 支持 `--dry-run` 只采集不邀请
- 支持邀请间隔和数量限制

## 环境要求

- Python 3.12+
- 一个可用的 Telegram 账号 session
- `API_ID` / `API_HASH`

## 安装

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -e .
```

## 环境变量

可以通过 `.env` 配置：

```env
API_ID=2040
API_HASH=b18441a1ff607e10a989891a5462e627
```

## 用法

只采集不邀请：

```bash
python -m klqzbot clone ^
  --session "C:\path\to\my.session" ^
  --source "https://t.me/source_group" ^
  --target "https://t.me/target_group" ^
  --limit 100 ^
  --dry-run
```

正式邀请：

```bash
python -m klqzbot clone ^
  --session "C:\path\to\my.session" ^
  --source "@source_group" ^
  --target "@target_group" ^
  --limit 50 ^
  --interval 45
```

## 下一步

- 多账号轮换邀请
- 邀请失败重试与风控冷却
- 本地任务记录
- GUI / Web 面板
