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

## 两种续期方式（强烈推荐面板版）

| 脚本 / workflow | 入口 | 单次收益 | 需要登录 | 冷却 |
|---|---|---|---|---|
| `app.py` / `renew.yml` | 公开投票页 `/servers/<slug>` | **+30 分钟** | 否 | 5 分钟 |
| `app_panel.py` / `renew_cookie.yml` | 登录后控制面板 `/server/<id>/console` | **+90 分钟** | 是（cookie） | 约 5 分钟 |

**面板版收益是投票版的 3 倍**，推荐用 `renew_cookie.yml`。两者不要同时启用。

### 面板续期机制（实测 2026-11-04）

```
打开 /server/<id>/console
  -> 显示 "22:31:19 remaining" 和两个按钮：
       "+ 90 min"      免费（Alpine.js, @click="extendedClicked()"）
       "+24h (no bal)" 付费 $0.15（余额不足时显示 no bal 且禁用）
点 "+ 90 min" -> 弹出 "Verify you're human to continue" Turnstile
通过验证 -> 剩余时间 +90 分钟，按钮变为 "04:55 cd" 倒计时
```
- **成功标志：按钮文本变成 `MM:SS cd`**（比读剩余时间更即时可靠）
- 冷却中按钮本身就显示 `xx:xx cd`，脚本会直接跳过
- 页面同样有广告遮罩 `#__g4f_adblock_overlay`，点击前必须移除（脚本已处理）

## 配置（仓库 Settings → Secrets and variables → Actions）

| Secret | 必填 | 说明 |
|---|---|---|
| `G4F_SERVERS` | ✅ | 要续期的服务器，逗号分隔。可写 `/servers/fun` 或完整 URL |
| `G4F_IGN` | ❌ | 投票显示昵称；留空则每次随机生成玩家风格昵称（如 `LunarFox42`） |
| `G4F_COOKIE` | ❌ | 登录 cookie（仅 `renew_cookie.yml` 用）。支持纯值或 `pelican_session=xxx` |
| `TG_BOT_TOKEN` | ❌ | Telegram Bot Token |
| `TG_CHAT_ID` | ❌ | Telegram Chat ID |
| `NODE_LINK` | ❌ | 代理节点(sing-box 步骤消费) |

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

### 关于登录 cookie（`G4F_COOKIE`）

- 来源：登录 `control.gaming4free.net` 后，开发者工具 → Application → Cookies → `pelican_session`
- **有效期 120 分钟**（Laravel 默认 `SESSION_LIFETIME`），但**滑动续期**——每次请求都会重置，
  所以只要运行间隔小于 120 分钟就不会掉线
- **cookie 不能绕过 Turnstile**：实测带 cookie POST 投票仍返回
  `422 Captcha verification failed`。它只提供登录态，验证码照样要过
- 能做的事：等价于 API key（可调 Client API、查 `renewal.seconds_remaining`）
- 若日志出现 `⚠️ 登录 cookie 已失效`，说明超过 120 分钟没运行或已在别处登出，需重新获取

### 关于 Client API（`api_spec.json`）

仓库附了完整 OpenAPI 规范（54 路径 / 68 操作）。**没有任何续期/投票端点**，
续期只能走 `POST /api/servers/<slug>/vote`（需 Turnstile）。
API 可用于查询状态、远程开关机、发控制台命令等。

- **提交方式：直接调用投票接口**（不点 UI 按钮）。实测前端点提交后会卡在 `Submitting…`、
  请求很慢甚至不发，直接 `fetch` 更快更可靠：
  `POST https://control.gaming4free.net/api/servers/<slug>/vote`
  body `{"cf-turnstile-response": "<token>", "voter_name": "<IGN>", "ad_watched": "0"}`
  （字段名必须是 `voter_name`；用 `ign` 服务端会忽略，名字不会出现在投票记录里）
- **冷却 5 分钟**（服务端 429 会明确返回 `Please wait 5 more minute(s)`）。
  因此支持单次运行连投多次：`G4F_VOTES_PER_RUN`（默认 2）、`G4F_VOTE_INTERVAL`（默认 330 秒）。
  每次投票 +30 分钟，2 次即 +60 分钟
- **广告遮罩**必须移除，否则 `#__g4f_adblock_overlay` 会拦截所有点击
- 投票昵称默认**随机生成玩家风格名字**（如 `EmberCrane10`），不会出现 bot 字样；
  想固定就设 `G4F_IGN`
- 结果判定三重校验：按钮变 `✓ VOTED` / 今日投票数增加 / 剩余时间增加；
  同时监听投票 API 响应区分「冷却」「达上限」与「真失败」
- 冷却或达上限不算失败（exit 0）；只有提交后完全无变化才 exit 1
- Turnstile 处理原则：**点一次就耐心等**（直连约 6 秒通过；代理下实测 79 秒、3 次点击）。
  Cloudflare 后台解题需 10~30 秒（代理/机房 IP 更慢），反复点击会中断并重置挑战 ——
  表现为"点了二十几次永远不通过"。脚本最多点 4 次、间隔 40 秒，仅长时间无结果才重试
- 若反复失败，多为当前出口 IP 被 CF 判定为高风险，建议换网络/节点，或直接让 runner 直连
- Telegram 通知为 HTML 富文本（状态图标 + 服务器分块 + 剩余时间变化 + 出口 IP），
  HTML 解析失败自动回退纯文本
- 失败时留下 `vote_no_modal_*.png` / `vote_captcha_fail_*.png` / `vote_no_change_*.png` 等截图
