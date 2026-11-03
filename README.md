# Gaming4Free 免费服务器自动投票续期

使用 GitHub Actions 定时给 [gaming4free.net](https://gaming4free.net) 上的免费游戏服务器**投票续期**。

## 站点机制（已实测确认）

| 环节 | 实现 |
|---|---|
| 续期方式 | **社区投票**，无需登录账号（服务器页公开可访问） |
| 页面 | `https://gaming4free.net/servers/<slug>` |
| 障碍 | 页面有全屏**广告拦截遮罩** `#__g4f_adblock_overlay`，会拦截所有点击（脚本自动移除） |
| 投票入口 | `VOTE + ADD 30 MIN`（`#sd-vote-btn`）→ 打开 `.vote-modal` 弹窗 |
| 弹窗内容 | `#vm-ign`（IGN，可选）、`#ts-widget`（Cloudflare Turnstile）、`#vm-submit`（默认禁用） |
| 提交 | 通过 Turnstile 后按钮启用 → `POST /api/servers/<slug>/vote` |
| 实际收益 | 实测按钮文案写 +30 MIN，**单次投票净加 90 分钟**（与站点历史记录 `+90m` 一致） |
| 上限 | **48h cap**，达上限后无法继续累加 |
| 冷却 | 有投票间隔限制，冷却中服务端拒绝且页面无提示（脚本按 API 响应判定） |

## 仓库结构

```
app.py                        # 投票主脚本
.github/workflows/renew.yml   # 定时工作流(每小时)
time.txt                      # 运行心跳(自动更新, 防止仓库闲置暂停)
.env.example                  # 本地配置模板
```

## 配置（仓库 Settings → Secrets and variables → Actions）

| Secret | 必填 | 说明 |
|---|---|---|
| `G4F_SERVERS` | ✅ | 要续期的服务器，逗号分隔。可写 `/servers/fun` 或完整 URL |
| `G4F_IGN` | ❌ | 投票显示昵称；留空则每次随机生成玩家风格昵称（如 `LunarFox42`） |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |
| `NODE_LINK` | ❌ | 代理节点(sing-box 步骤消费)，支持 `vless://` `vmess://` `trojan://` `tuic://` `anytls://` `hysteria2://` `socks5://`；不配则直连 |

## 使用

1. 建仓库并上传本目录内容
2. 配置上表 Secrets（`G4F_SERVERS` 必填）
3. Actions 手动触发 `workflow_dispatch` 测试
4. 默认每小时运行一次：已达上限或冷却中自动跳过（exit 0）

## 本地运行

```bash
pip install patchright requests
cp .env.example .env   # 填服务器路径
python app.py
```

## 说明

- **广告遮罩**必须移除，否则 `#__g4f_adblock_overlay` 会拦截所有点击（脚本用 JS 移除）
- 投票昵称默认**随机生成玩家风格名字**（如 `EmberCrane10`），不会出现 bot 字样；
  想固定就设 `G4F_IGN`
- 结果判定三重校验：按钮变 `✓ VOTED` / 今日投票数增加 / 剩余时间增加；
  同时监听投票 API 响应区分「冷却」「达上限」与「真失败」
- 冷却或达上限不算失败（exit 0）；只有提交后完全无变化才 exit 1
- Turnstile 点击节奏 5 秒一次（实测一次通过）。若验证框迟迟不出现，会区分
  "页面本就无验证" 与 "组件被网络拦截未渲染"，后者直接放弃并提示检查线路
- Telegram 通知为 HTML 富文本（状态图标 + 服务器分块 + 剩余时间变化 + 出口 IP），
  HTML 解析失败自动回退纯文本
- 失败时留下 `vote_no_modal_*.png` / `vote_captcha_fail_*.png` / `vote_no_change_*.png` 等截图
- ⚠️ 短时间内频繁打开页面会触发 Cloudflare 风控（Turnstile 不渲染）。Actions 每小时一次无碍
