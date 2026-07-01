# klqzbot

`klqzbot` 是一个独立的 Telegram 群内容同步工具仓库。

当前主目标是：

1. 监听 A 群新消息
2. 抓取消息文案、媒体、按钮
3. 重新发送到你管理的 B 群
4. 不显示转发来源

另外保留了一个早期的 `clone` 子命令骨架，后续是否继续扩展可再定。

## 当前能力

- 支持实时监听源群新消息
- 支持把文本、媒体、按钮重新发送到目标群
- 通过“重发”而不是“转发”来隐藏来源
- 支持 `@username`、`https://t.me/...`、`https://t.me/+inviteHash` 形式的群引用

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

### 实时同步消息

```bash
python -m klqzbot mirror ^
  --session "C:\path\to\my.session" ^
  --source "https://t.me/source_group" ^
  --target "https://t.me/target_group"
```

启动后会持续监听 A 群；只要 A 群有新消息，就会直接抓取并同步发送到 B 群。

### 旧的成员克隆骨架

只采集不邀请：

```bash
python -m klqzbot clone ^
  --session "C:\path\to\my.session" ^
  --source "@source_group" ^
  --target "@target_group" ^
  --limit 100 ^
  --dry-run
```

## 下一步

- 同步消息编辑
- 同步删除
- 多源群到多目标群
- 本地规则过滤
- GUI / Web 面板
