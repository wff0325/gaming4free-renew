#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gaming4free.net 免费游戏服务器投票续期脚本
参考架构: 同工作区 hyehost / katabump / bot-hosting / orihost 四套脚本

站点机制(已实测确认, 2026-11-03):
  - 续期方式是**社区投票**(不需要登录账号, 服务器页公开可访问)
  - 页面: https://gaming4free.net/servers/<slug>
  - 顶部有广告拦截遮罩 #__g4f_adblock_overlay (全屏, 挡住点击), 脚本会移除
  - 点 "VOTE + ADD 30 MIN" (#sd-vote-btn) 打开 .vote-modal 弹窗
      弹窗含: #vm-ign (IGN, 可选) / #ts-widget (Cloudflare Turnstile) / #vm-submit (默认禁用)
  - 通过 Turnstile 后 #vm-submit 启用 -> 点击 -> POST /api/servers/<slug>/vote
  - 实测结果: 投票后按钮变 "✓ VOTED — +90m", 倒计时 +30 分钟, VOTES TODAY +1
    (按钮文案写 +30 MIN, 实际单次投票净加 90 分钟, 与站点历史记录 "+90m" 一致)
  - 上限: 48h cap, 剩余时间达上限后无法继续累加
  - IGN 只是显示名, 默认随机生成玩家风格昵称(避免出现 bot 字样)

用法:
    本地: .env 写 G4F_SERVERS (服务器页路径/URL, 逗号分隔), 然后 python app.py
    Actions: Secrets 配 G4F_SERVERS (+ 可选 G4F_IGN/TG_*/NODE_LINK)

配置项(环境变量):
    G4F_SERVERS   服务器页, 逗号分隔 (必填)
                  例: /servers/fun 或 /servers/fun,/servers/creeper-realm
                  或完整 URL: https://gaming4free.net/servers/fun
    G4F_IGN       投票显示名 (可选; 留空则每次随机生成玩家昵称)
    TG_BOT_TOKEN  Telegram Bot Token (可选)
    TG_CHAT_ID    Telegram Chat ID   (可选)
    IS_PROXY      是否使用代理 (默认 false; workflow 代理步骤会写入)
    PROXY_SERVER  代理地址 (默认 http://127.0.0.1:1081)
    BROWSER_CHANNEL 浏览器通道 (msedge/chrome, 空=自动探测)
    SECURITY_TIMEOUT CF 验证等待秒数 (默认 240)
"""

import os, re, sys, time, random, shutil, json

# ==================== 本地 .env 加载(不提交仓库) ====================
def _load_local_env():
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if not os.path.exists(env_path):
        return
    try:
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                k, v = k.strip(), v.strip().strip('"').strip("'")
                if k and v and not os.environ.get(k):
                    os.environ[k] = v
    except Exception:
        pass

_load_local_env()

# ==================== CONFIG ====================
SERVERS_RAW  = (os.environ.get('G4F_SERVERS') or os.environ.get('SERVERS') or "").strip()
IGN          = (os.environ.get('G4F_IGN') or "").strip()
TG_BOT_TOKEN = os.environ.get('TG_BOT_TOKEN') or ""
TG_CHAT_ID   = os.environ.get('TG_CHAT_ID') or ""
IS_PROXY     = os.environ.get('IS_PROXY', 'false').lower() == 'true'
PROXY_SERVER = (os.environ.get('PROXY_SERVER') or "http://127.0.0.1:1081").strip()
# 本脚本目标是境外公开站点, runner 直连即可; 机房代理 IP 反而更容易被 Cloudflare 风控。
# 设 G4F_NO_PROXY=true 可强制忽略代理(即使 workflow 启动了 sing-box)。
if os.environ.get('G4F_NO_PROXY', '').lower() == 'true':
    IS_PROXY = False
BROWSER_CHANNEL = os.environ.get('BROWSER_CHANNEL') or ""
SECURITY_TIMEOUT = int(os.environ.get('SECURITY_TIMEOUT') or "240")
# 单次运行连续投票次数与间隔(实测冷却 5 分钟)
VOTES_PER_RUN = int(os.environ.get('G4F_VOTES_PER_RUN') or "2")
VOTE_INTERVAL = int(os.environ.get('G4F_VOTE_INTERVAL') or "330")

REQUESTS_PROXIES = {"http": PROXY_SERVER, "https": PROXY_SERVER} if IS_PROXY else None

BASE_URL = "https://gaming4free.net"

try:
    from patchright.sync_api import sync_playwright
    USING_PATCHRIGHT = True
except ImportError:
    from playwright.sync_api import sync_playwright
    USING_PATCHRIGHT = False

import requests

# ==================== 常量 ====================
CF_IFRAME_SELECTOR = 'iframe[src*="challenges.cloudflare.com"]'
SECURITY_TITLE_HINTS = ('security verification', 'just a moment', 'attention required',
                        'checking your browser', 'one more step')
DEFAULT_UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
              '(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36')

CAP_HOURS = 48      # 站点上限

# 随机玩家昵称(避免出现 bot 字样)
NAME_A = ["Lunar", "Nova", "Pixel", "Shadow", "Crimson", "Frost", "Ember", "Storm",
          "Echo", "Neon", "Solar", "Vivid", "Quiet", "Rapid", "Silver", "Amber",
          "Cobalt", "Maple", "Orbit", "Drift", "Aster", "Blaze", "Cedar", "Dusk",
          "Flint", "Glow", "Haze", "Indigo", "Jade", "Kite", "Lumen", "Mist"]
NAME_B = ["Fox", "Panda", "Wolf", "Otter", "Falcon", "Tiger", "Lynx", "Raven",
          "Crane", "Bison", "Koi", "Moth", "Hawk", "Bear", "Seal", "Heron",
          "Badger", "Finch", "Gecko", "Ibis", "Lark", "Marten", "Newt", "Puma"]

def random_ign():
    """生成玩家风格随机昵称, 如 LunarFox42"""
    return f"{random.choice(NAME_A)}{random.choice(NAME_B)}{random.randint(2, 99)}"

def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {message}", flush=True)

def _parse_servers(raw):
    out = []
    for part in re.split(r'[,\s]+', raw or ""):
        part = part.strip()
        if not part:
            continue
        if part.startswith("http"):
            url = part
        elif part.startswith("/"):
            url = BASE_URL + part
        else:
            url = BASE_URL + "/servers/" + part
        slug = url.rstrip("/").split("/")[-1]
        out.append({"slug": slug, "url": url})
    return out

# ==================== 通知 ====================
def _status_icon(result):
    if result == "NOT_TIME":
        return "🕐", "已达上限/冷却中"
    if result is True:
        return "✅", "投票成功"
    return "❌", "投票失败"

def build_notification_text(results, current_ip="未知", ign=""):
    """results: [(slug, result, before, after)]  before/after 为 '11h 5m' 形式"""
    now = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(time.time() + 8 * 3600))
    if any(r[1] is False for r in results):
        icon, head = "⚠️", "Gaming4Free 投票报告（有失败）"
    elif all(r[1] == "NOT_TIME" for r in results):
        icon, head = "🕐", "Gaming4Free 投票检查"
    else:
        icon, head = "✅", "Gaming4Free 投票报告"

    lines = [
        f"<b>{icon} {head}</b>",
        "━━━━━━━━━━━━━━━━━━",
        f"<b>投票昵称</b>　<code>{ign}</code>",
        "",
    ]
    for slug, result, before, after in results:
        ic, label = _status_icon(result)
        lines.append(f"<b>🎮 {slug}</b>")
        lines.append(f"　{ic} {label}")
        if result is True and before != after:
            lines.append(f"　剩余　{before} → <b>{after}</b>")
        else:
            lines.append(f"　剩余　{after}")
        lines.append("")
    lines += [
        "━━━━━━━━━━━━━━━━━━",
        f"<b>🌐 出口 IP</b>　<code>{current_ip}</code>",
        f"<b>🕒 时间</b>　{now} (UTC+8)",
        f"<i>上限 {CAP_HOURS}h · 单次投票约 +90 分钟</i>",
    ]
    return "\n".join(lines)

def send_telegram_notification(results, current_ip="未知", ign=""):
    if not TG_BOT_TOKEN or not TG_CHAT_ID:
        log("Telegram 未配置，跳过通知")
        return
    text = build_notification_text(results, current_ip, ign)
    url = f"https://api.telegram.org/bot{TG_BOT_TOKEN}/sendMessage"
    try:
        r = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text,
                                     "parse_mode": "HTML",
                                     "disable_web_page_preview": True},
                          timeout=10, proxies=REQUESTS_PROXIES)
        if r.status_code == 200:
            log("Telegram 通知发送成功")
        else:
            log(f"Telegram HTML 通知失败({r.status_code})，回退纯文本: {r.text[:120]}")
            plain = re.sub(r'<[^>]+>', '', text)
            r2 = requests.post(url, json={"chat_id": TG_CHAT_ID, "text": plain},
                               timeout=10, proxies=REQUESTS_PROXIES)
            log("Telegram 通知发送成功(纯文本)" if r2.status_code == 200
                else f"Telegram 通知失败: {r2.text[:200]}")
    except Exception as e:
        log(f"Telegram 通知异常: {e}")

def get_current_ip():
    try:
        r = requests.get("https://api.ip.sb/ip", proxies=REQUESTS_PROXIES, timeout=15)
        if r.status_code == 200:
            return r.text.strip()
    except Exception as e:
        log(f"获取出口IP失败: {e}")
    return "获取失败"

# ==================== Cloudflare 过盾 / Turnstile ====================
def _has_interstitial_iframe(page):
    try:
        for h in page.query_selector_all(CF_IFRAME_SELECTOR):
            if '/turnstile/' not in (h.get_attribute('src') or ''):
                return True
    except Exception:
        pass
    return False

def _is_security_check_page(page):
    try:
        title = (page.title() or '').lower()
        if any(h in title for h in SECURITY_TITLE_HINTS):
            return True
    except Exception:
        return True
    try:
        return page.evaluate("""() => {
            const t = document.body ? document.body.innerText.slice(0,5000).toLowerCase() : '';
            return t.includes('verify you are human') || t.includes('checking your browser');
        }""")
    except Exception:
        return True

def _find_visible_cf_frames(page):
    out = []
    try:
        for h in page.query_selector_all(CF_IFRAME_SELECTOR):
            try:
                b = h.bounding_box()
                if b and b['width'] > 10 and b['height'] > 10:
                    out.append(h)
            except Exception:
                continue
    except Exception:
        pass
    return out

def _click_cf_checkbox(page, frame_el=None):
    """点 Turnstile 复选框; 返回是否点到"""
    for frame in page.frames:
        if 'challenges.cloudflare.com' not in (frame.url or ''):
            continue
        try:
            cb = frame.locator('input[type="checkbox"]')
            if cb.count() == 0:
                continue
            try:
                cb.first.click(force=True, timeout=3000)
                return True
            except Exception:
                try:
                    cb.first.dispatch_event('click')
                    return True
                except Exception:
                    pass
        except Exception:
            continue
    if frame_el is None:
        return False
    try:
        frame_el.scroll_into_view_if_needed(timeout=3000)
        box = frame_el.bounding_box()
        if not box or box['width'] < 10:
            return False
        x = box['x'] + 28
        y = box['y'] + box['height'] / 2
        page.mouse.move(x - random.uniform(40, 70), y + random.uniform(-10, 10))
        time.sleep(random.uniform(0.2, 0.5))
        page.mouse.move(x, y)
        time.sleep(random.uniform(0.1, 0.25))
        page.mouse.down()
        time.sleep(random.uniform(0.05, 0.12))
        page.mouse.up()
        return True
    except Exception:
        return False

# 缓存最近一次拿到的 Turnstile token。
# 原因: 前端可能在验证成功后立刻自动提交并重置 widget, 导致随后读取时 token 已被消费。
TOKEN_CACHE = {"v": None, "at": 0}

def _get_turnstile_token(page, allow_cache=False):
    """读取 Turnstile token。allow_cache=True 时, 若实时读取为空则回退到最近缓存
    (token 有效期约 300 秒, 缓存 240 秒内仍可用)"""
    tok = None
    try:
        tok = page.evaluate("""() => {
            if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                let t = null;
                try { t = window.turnstile.getResponse(); } catch(e) {}
                if (t && t.length > 30) return t;
                for (let i = 0; i < 5; i++) {
                    try { t = window.turnstile.getResponse(String(i)); } catch(e) { t = null; }
                    if (t && t.length > 30) return t;
                }
            }
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            if (inp && inp.value && inp.value.length > 30) return inp.value;
            return null;
        }""")
    except Exception:
        tok = None
    if tok:
        TOKEN_CACHE.update({"v": tok, "at": time.time()})
        return tok
    if allow_cache and TOKEN_CACHE["v"] and (time.time() - TOKEN_CACHE["at"]) < 240:
        return TOKEN_CACHE["v"]
    return None

def _captcha_pending(page):
    try:
        return page.evaluate("""() => {
            const hasWidget = !!document.querySelector(
                '[id*="cf-chl-widget"], .cf-turnstile, #cf-turnstile, input[name="cf-turnstile-response"]');
            const inp = document.querySelector('input[name="cf-turnstile-response"]');
            const hasToken = !!(inp && inp.value && inp.value.length > 30);
            return hasWidget && !hasToken;
        }""")
    except Exception:
        return False

def handle_cloudflare(page, timeout=None):
    if timeout is None:
        timeout = SECURITY_TIMEOUT
    def active():
        return _has_interstitial_iframe(page) or _is_security_check_page(page)
    if not active():
        return True
    log("检测到 Cloudflare 安全验证...")
    time.sleep(2)
    start = time.time()
    last_click = 0
    clear = 0
    while time.time() - start < timeout:
        if not active():
            clear += 1
            if clear >= 2:
                return True
            time.sleep(1)
            continue
        clear = 0
        if time.time() - last_click > 6:
            frames = _find_visible_cf_frames(page)
            if frames:
                log("点击 CF 验证...")
                if _click_cf_checkbox(page, frames[-1]):
                    last_click = time.time()
                    time.sleep(random.uniform(2, 4))
                    continue
        time.sleep(1)
    log("CF 验证超时")
    return False

def solve_turnstile(page, timeout=180, max_clicks=3, grace=30):
    """处理页面内嵌 Turnstile(投票弹窗内)。

    核心原则: **点一次就耐心等**。
    Turnstile 点击后由 Cloudflare 后台解题, 需 10~30 秒(机房/代理 IP 更慢);
    解题过程中重复点击会中断并重置挑战, 表现为"点了几十次永远不通过"。
    仅在 (a) 检测到验证过期/失败, 或 (b) 静默等待超过 grace 秒仍无 token 时,
    才做有限次重试(最多 max_clicks 次)。
    """
    if _get_turnstile_token(page):
        return True

    # 1) 等组件渲染(弹窗刚打开时 iframe 可能还没插入)
    frames = _find_visible_cf_frames(page)
    if not frames:
        for _ in range(20):
            time.sleep(1.5)
            if _get_turnstile_token(page):
                return True
            frames = _find_visible_cf_frames(page)
            if frames:
                break
        if not frames:
            if _captcha_pending(page):
                log("⚠️ Turnstile 占位存在但未渲染（CF 资源可能被拦截）")
                return False
            return True

    # 2) 先静等几秒: Turnstile 有时无需点击会自动通过
    for _ in range(5):
        if _get_turnstile_token(page):
            log("Turnstile 自动通过")
            return True
        time.sleep(1)

    # 3) 点一次, 然后耐心等
    log("处理 Turnstile 人机验证（点击后耐心等待解题，不重复点击）...")
    start = time.time()
    clicks = 0
    last_click = 0
    while time.time() - start < timeout:
        if _get_turnstile_token(page):
            log(f"Turnstile 验证通过（点击 {clicks} 次，用时 {int(time.time() - start)}s）")
            return True

        frames = _find_visible_cf_frames(page)
        if not frames:
            time.sleep(2)
            if _get_turnstile_token(page):
                log("Turnstile 验证通过")
                return True
            log("Turnstile 组件消失且无 token")
            break

        if clicks == 0:
            log("点击 Turnstile 复选框（第 1 次）...")
            _click_cf_checkbox(page, frames[-1])
            clicks = 1
            last_click = time.time()
            time.sleep(4)      # 点完立刻静默, 不打断解题
            continue

        waited = time.time() - last_click
        if clicks < max_clicks and waited > grace:
            log(f"已等待 {int(waited)}s 仍无结果，重试点击（第 {clicks + 1} 次）...")
            _click_cf_checkbox(page, frames[-1])
            clicks += 1
            last_click = time.time()
            time.sleep(4)
            continue

        time.sleep(2)          # 静默等待, 不打扰

    tok = bool(_get_turnstile_token(page))
    if tok:
        log("Turnstile 验证通过")
    else:
        log(f"Turnstile 未通过（点击 {clicks} 次，共等待 {int(time.time() - start)}s）")
        log("   若反复失败，多半是当前出口 IP 被 Cloudflare 判定为高风险，建议更换代理节点")
    return tok

# ==================== 浏览器 ====================
def _detect_channel():
    if BROWSER_CHANNEL:
        return BROWSER_CHANNEL
    if sys.platform == 'win32':
        for ch, paths in {
            'msedge': [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
                       r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"],
            'chrome': [r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                       r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"],
        }.items():
            if any(os.path.exists(p) for p in paths):
                return ch
        return None
    if shutil.which("google-chrome") or shutil.which("google-chrome-stable"):
        return "chrome"
    if shutil.which("microsoft-edge"):
        return "msedge"
    return "chrome"

def open_browser(p):
    proxy_arg = {"server": PROXY_SERVER} if IS_PROXY else None
    channel = _detect_channel()
    if USING_PATCHRIGHT and channel:
        try:
            browser = p.chromium.launch(channel=channel, headless=False,
                                        args=['--disable-infobars'], proxy=proxy_arg)
            ctx = browser.new_context(viewport={'width': 1440, 'height': 900}, proxy=proxy_arg)
            # 预置广告遮罩确认标记(站点用 sessionStorage.__g4f_adblock), 减少遮罩弹出
            ctx.add_init_script("try { sessionStorage.setItem('__g4f_adblock','1'); } catch(e) {}")
            log(f"启动反检测浏览器 (patchright + {channel})")
            return browser, ctx.new_page()
        except Exception as e:
            log(f"patchright 启动失败: {e}")
    log("使用内置 chromium (过 CF 能力较弱)")
    browser = p.chromium.launch(headless=False,
        args=['--no-sandbox', '--disable-blink-features=AutomationControlled', '--disable-infobars'])
    ctx = browser.new_context(viewport={'width': 1440, 'height': 900},
                              user_agent=DEFAULT_UA, proxy=proxy_arg)
    page = ctx.new_page()
    page.add_init_script("try { Object.defineProperty(navigator,'webdriver',{get:()=>undefined}); } catch(e) {}")
    return browser, page

# ==================== 页面状态 ====================
def _dismiss_adblock_overlay(page):
    """移除广告拦截全屏遮罩(否则会拦截所有点击)"""
    try:
        removed = page.evaluate("""() => {
            let n = 0;
            document.querySelectorAll('#__g4f_adblock_overlay').forEach(o => { o.remove(); n++; });
            return n;
        }""")
        if removed:
            log(f"已移除广告拦截遮罩 ({removed})")
        return removed > 0
    except Exception:
        return False

def read_remaining(page):
    """读取剩余时间, 返回 (显示文本, 小时数)"""
    try:
        data = page.evaluate("""() => {
            const out = {text: null, hours: null, votesToday: null, voted: false, cap: null};
            const t = document.body.innerText;
            const m = t.match(/expires in\\s*([0-9]+)\\s*h\\s*([0-9]+)\\s*m/i);
            if (m) {
                out.text = `${m[1]}h ${m[2]}m`;
                out.hours = parseInt(m[1]) + parseInt(m[2]) / 60;
            } else {
                const m2 = t.match(/expires in\\s*([0-9]+)\\s*m/i);
                if (m2) { out.text = `${m2[1]}m`; out.hours = parseInt(m2[1]) / 60; }
            }
            const m3 = t.match(/VOTES TODAY\\s*\\n?\\s*(\\d+)/i);
            if (m3) out.votesToday = parseInt(m3[1]);
            const m4 = t.match(/(\\d+)\\s*h\\s*cap/i);
            if (m4) out.cap = parseInt(m4[1]);
            const btn = document.querySelector('#sd-vote-btn');
            if (btn && /VOTED/i.test(btn.innerText || '')) out.voted = true;
            return out;
        }""")
        return data.get("text") or "未知", data.get("hours"), data
    except Exception as e:
        log(f"读取剩余时间异常: {e}")
        return "未知", None, {}

def _vote_button_state(page):
    try:
        return page.evaluate("""() => {
            const b = document.querySelector('#sd-vote-btn');
            if (!b) return {found: false};
            return {found: true, text: (b.innerText||'').trim(), disabled: !!b.disabled,
                    voted: /VOTED/i.test(b.innerText||'')};
        }""")
    except Exception:
        return {"found": False}

# ==================== 投票 API 响应捕获 ====================
LAST_VOTE_RESP = {"status": None, "body": None, "at": 0}
CONSOLE_LOG = []

def _attach_vote_listener(page):
    """监听投票 API 响应 + 前端报错, 用于精确判断成功/冷却/失败(页面无可见提示)"""
    def on_resp(r):
        try:
            if '/vote' not in r.url.lower() or r.request.method != 'POST':
                return
            try:
                body = r.text()
            except Exception:
                body = ""
            LAST_VOTE_RESP.update({"status": r.status, "body": body, "at": time.time()})
            log(f"投票API: {r.status} {body[:300]}")
        except Exception:
            pass
    page.on("response", on_resp)

    def on_console(m):
        try:
            if m.type in ("error", "warning"):
                CONSOLE_LOG.append(f"{m.type}: {(m.text or '')[:180]}")
                del CONSOLE_LOG[:-20]
        except Exception:
            pass
    page.on("console", on_console)

def _api_vote(page, slug, ign):
    """在页面内直接 fetch 投票接口(实测比点提交按钮更可靠、更快)。

    请求体格式(实测确认): {"ign": "...", "cf-turnstile-response": "<token>"}
    成功返回: {"success":true,"message":"30 minutes added!","minutes_per_vote":30,
               "hours_remaining":12.2,"vote_count_today":16,...}
    冷却返回: 429 {"error":"Please wait 5 more minute(s) — or watch an ad to extend sooner."}
    """
    cached = TOKEN_CACHE["v"] if (TOKEN_CACHE["v"] and (time.time() - TOKEN_CACHE["at"]) < 240) else None
    # 同时从页面取一次 token(Python 侧), 供 request 兜底使用
    py_token = _get_turnstile_token(page, allow_cache=True)
    try:
        # 关键: 取 token 与提交放在**同一次 JS 执行**里, 避免 token 在两次调用间被前端重置
        res = page.evaluate("""async (args) => {
            const {slug, ign, cached} = args;
            let token = null;
            try {
                if (window.turnstile && typeof window.turnstile.getResponse === 'function') {
                    token = window.turnstile.getResponse() || null;
                    if (!token) {
                        for (let i = 0; i < 5; i++) {
                            try { token = window.turnstile.getResponse(String(i)); } catch(e) {}
                            if (token && token.length > 30) break;
                        }
                    }
                }
                if (!token) {
                    const inp = document.querySelector('input[name="cf-turnstile-response"]');
                    if (inp && inp.value && inp.value.length > 30) token = inp.value;
                }
            } catch (e) {}
            if (!token && cached) token = cached;
            if (!token) return {error: 'no-token'};
            try {
                const r = await fetch(
                    `https://control.gaming4free.net/api/servers/${slug}/vote`,
                    {method: 'POST',
                     headers: {'Content-Type': 'application/json', 'Accept': 'application/json'},
                     body: JSON.stringify({ign: ign, 'cf-turnstile-response': token})});
                return {status: r.status, body: (await r.text()).slice(0, 500),
                        tokenLen: token.length, usedCache: !!(cached && token === cached)};
            } catch (e) { return {error: String(e).slice(0, 200)}; }
        }""", {"slug": slug, "ign": ign, "cached": cached})
        log(f"[{slug}] 接口返回: {json.dumps(res, ensure_ascii=False)[:400]}")
        if res and res.get("body"):
            LAST_VOTE_RESP.update({"status": res.get("status"),
                                   "body": res["body"], "at": time.time()})
            log(f"投票API: {res.get('status')} {res['body'][:300]}")
            return res

        # 兜底2: 页面内 fetch 失败(常见 "TypeError: Failed to fetch" = CORS/被拦),
        # 改用 Playwright 的 request context 直接发请求(不受浏览器 CORS 限制)
        if py_token or cached:
            try:
                import json as _json
                r = page.request.post(
                    f"https://control.gaming4free.net/api/servers/{slug}/vote",
                    data=_json.dumps({"ign": ign,
                                      "cf-turnstile-response": py_token or cached}),
                    headers={"Content-Type": "application/json",
                             "Accept": "application/json",
                             "Origin": BASE_URL,
                             "Referer": BASE_URL + "/"})
                body = r.text()[:400]
                log(f"[{slug}] request 兜底: {r.status} {body[:200]}")
                if body:
                    LAST_VOTE_RESP.update({"status": r.status, "body": body, "at": time.time()})
                return {"status": r.status, "body": body, "via": "request"}
            except Exception as e:
                log(f"[{slug}] request 兜底失败: {e}")
        return res
    except Exception as e:
        log(f"API 提交异常: {e}")
        return None

def _vote_success_text(page):
    """弹窗内出现成功文案: '30 minutes added! Thanks for supporting the server, xxx!'"""
    try:
        return page.evaluate("""() => {
            const m = document.querySelector('.vote-modal');
            const t = m ? (m.innerText || '') : (document.body ? document.body.innerText : '');
            return /minutes?\\s+added|thanks for supporting/i.test(t);
        }""")
    except Exception:
        return False

def _vote_api_data():
    """解析投票 API 的 JSON 响应(最权威的数据源)
    成功样例: {"success":true,"message":"30 minutes added! ...",
               "minutes_per_vote":30,"hours_remaining":12.3,
               "vote_count_today":15,"vote_count_total":15}
    """
    body = LAST_VOTE_RESP.get("body") or ""
    if not body:
        return {}
    try:
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}

def _fmt_hours(h):
    if h is None:
        return None
    try:
        h = float(h)
        return f"{int(h)}h {int(round((h - int(h)) * 60))}m"
    except Exception:
        return None

def _vote_api_verdict():
    """解析投票 API 响应: 'ok' / 'cooldown' / 'cap' / 'unknown'"""
    data = _vote_api_data()
    if data:
        if data.get("success") is True:
            return "ok"
        low = json.dumps(data, ensure_ascii=False).lower()
    else:
        low = (LAST_VOTE_RESP.get("body") or "").lower()
        if not low:
            return "unknown"
    if any(k in low for k in ("cooldown", "too soon", "wait", "try again", "rate limit", "slow down")):
        return "cooldown"
    if any(k in low for k in ("cap", "max", "limit reached", "full")):
        return "cap"
    if any(k in low for k in ('"ok":true', '"success":true', "added", "voted")):
        return "ok"
    return "unknown"

# ==================== 投票 ====================
def vote_server(page, server, ign):
    """给单台服务器投票。返回 (结果, 前剩余, 后剩余)"""
    slug = server["slug"]
    url = server["url"]
    try:
        log(f"[{slug}] 打开服务器页...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        time.sleep(3)

        _dismiss_adblock_overlay(page)
        time.sleep(1)

        before_txt, before_h, info = read_remaining(page)
        votes_before = info.get("votesToday")
        log(f"[{slug}] 剩余 {before_txt}，今日投票 {votes_before}，上限 {info.get('cap')}h")

        # 已达上限
        if before_h is not None and before_h >= CAP_HOURS:
            log(f"[{slug}] 剩余已达 {CAP_HOURS}h 上限，跳过")
            return "NOT_TIME", before_txt, before_txt

        # 已投过(按钮变 VOTED 且有冷却)
        st = _vote_button_state(page)
        if st.get("voted"):
            log(f"[{slug}] 按钮显示已投票（{st.get('text')}），本次跳过")
            return "NOT_TIME", before_txt, before_txt
        if st.get("disabled"):
            log(f"[{slug}] 投票按钮禁用（{st.get('text')}）")
            return "NOT_TIME", before_txt, before_txt

        # 打开投票弹窗
        log(f"[{slug}] 点击 VOTE 按钮...")
        page.locator('#sd-vote-btn').first.click(timeout=10000)
        time.sleep(3)

        modal = page.evaluate("""() => {
            const m = document.querySelector('.vote-modal');
            if (!m) return null;
            const r = m.getBoundingClientRect();
            return {visible: r.width > 0 && r.height > 0,
                    text: (m.innerText||'').trim().slice(0, 200)};
        }""")
        if not modal or not modal.get("visible"):
            log(f"[{slug}] 投票弹窗未出现: {modal}")
            _dismiss_adblock_overlay(page)
            page.screenshot(path=f"vote_no_modal_{slug}.png")
            return False, before_txt, before_txt

        # 填 IGN(可选字段, 用随机玩家昵称)
        try:
            ign_in = page.locator('#vm-ign')
            if ign_in.count() > 0:
                ign_in.fill(ign)
                log(f"[{slug}] 投票昵称: {ign}")
        except Exception as e:
            log(f"[{slug}] 填写昵称失败(可忽略): {e}")
        time.sleep(1)

        # 过 Turnstile(失败则刷新页面重试一次: 组件偶发不渲染)
        if not solve_turnstile(page, timeout=120):
            log(f"[{slug}] Turnstile 未渲染/未通过，刷新页面重试一次...")
            try:
                page.reload(wait_until="domcontentloaded", timeout=60000)
                handle_cloudflare(page)
                time.sleep(5)
                _dismiss_adblock_overlay(page)
                time.sleep(1)
                page.locator('#sd-vote-btn').first.click(timeout=10000)
                time.sleep(4)
                try:
                    page.locator('#vm-ign').fill(ign)
                except Exception:
                    pass
                time.sleep(1)
            except Exception as e:
                log(f"[{slug}] 刷新重试异常: {e}")
            if not solve_turnstile(page, timeout=150):
                log(f"[{slug}] Turnstile 仍未通过，放弃本次")
                page.screenshot(path=f"vote_captcha_fail_{slug}.png")
                return False, before_txt, before_txt

        # 等提交按钮启用
        enabled = False
        for _ in range(15):
            d = page.evaluate("() => { const b=document.querySelector('#vm-submit'); return b? b.disabled : null; }")
            if d is False:
                enabled = True
                break
            time.sleep(2)
        if not enabled:
            log(f"[{slug}] 提交按钮未启用")
            page.screenshot(path=f"vote_submit_disabled_{slug}.png")
            return False, before_txt, before_txt

        # 优先直接调用投票接口(实测: 前端点提交会卡在 "Submitting…", 请求很慢;
        # 直接 fetch 更快更可靠。请求体格式已实测确认)
        log(f"[{slug}] 提交投票（直接调用接口）...")
        LAST_VOTE_RESP.update({"status": None, "body": None, "at": 0})
        _api_vote(page, slug, ign)

        # API 已明确成功时立即返回, 不必再等页面刷新(省 20 秒)
        api_early = _vote_api_data()
        if api_early.get("success") is True:
            gain = api_early.get("minutes_per_vote")
            after_early = _fmt_hours(api_early.get("hours_remaining")) or before_txt
            log(f"[{slug}] 投票成功! {before_txt} -> {after_early}"
                + (f"（+{gain} 分钟）" if gain else ""))
            return True, before_txt, after_early

        if not LAST_VOTE_RESP.get("body"):
            # 兜底: 点击提交按钮(前端慢, 最多等 15 秒)
            log(f"[{slug}] 直接接口无响应，改用点击提交按钮...")
            try:
                page.locator('#vm-submit').first.click(timeout=10000)
            except Exception as e:
                log(f"[{slug}] 点击提交失败: {e}")
            for _ in range(15):
                time.sleep(2)
                if LAST_VOTE_RESP.get("body"):
                    break

        # 等待成功文案或票数刷新(页面提示有延迟, 最多等 20 秒)
        success_text = False
        after_txt, after_h, info2 = before_txt, before_h, info
        votes_after = votes_before
        for i in range(10):
            time.sleep(2)
            success_text = _vote_success_text(page)
            after_txt, after_h, info2 = read_remaining(page)
            votes_after = info2.get("votesToday")
            if success_text:
                break
            if votes_before is not None and votes_after is not None and votes_after > votes_before:
                break

        st2 = _vote_button_state(page)
        verdict = _vote_api_verdict()
        api = _vote_api_data()
        # API 的 hours_remaining 最准确(页面倒计时显示有延迟)
        api_hours = api.get("hours_remaining")
        if api_hours is not None:
            after_txt = _fmt_hours(api_hours) or after_txt
            try:
                after_h = float(api_hours)
            except Exception:
                pass
        if api.get("vote_count_today") is not None:
            votes_after = api["vote_count_today"]
        log(f"[{slug}] 投票后: 剩余 {after_txt}，今日投票 {votes_after}，"
            f"按钮 {st2.get('text')!r}，成功文案 {success_text}，API判定 {verdict}")

        voted_ok = bool(st2.get("voted"))
        votes_up = (votes_before is not None and votes_after is not None
                    and votes_after > votes_before)
        time_up = (before_h is not None and after_h is not None and after_h > before_h)

        if success_text or voted_ok or votes_up or time_up or verdict == "ok":
            gain = api.get("minutes_per_vote")
            log(f"[{slug}] 投票成功! {before_txt} -> {after_txt}"
                + (f"（+{gain} 分钟）" if gain else ""))
            return True, before_txt, after_txt

        if verdict in ("cooldown", "cap"):
            log(f"[{slug}] 服务端拒绝（{verdict}），视为冷却/上限，跳过")
            return "NOT_TIME", before_txt, after_txt

        # API 返回 HTML(通常 403) = Cloudflare 拦截页, 说明当前出口 IP 被风控
        body_raw = (LAST_VOTE_RESP.get("body") or "").lstrip()
        if body_raw.startswith("<"):
            log(f"[{slug}] 接口返回 HTML 拦截页（HTTP {LAST_VOTE_RESP.get('status')}），"
                f"当前出口 IP 可能被 Cloudflare 风控，本次跳过")
            return "NOT_TIME", before_txt, after_txt

        # 提交后既无成功标志也无 API 响应: 多半是前端冷却拦截(请求未发出)
        if not LAST_VOTE_RESP.get("body"):
            log(f"[{slug}] 提交后未收到投票 API 响应，判断为冷却期，本次跳过")
            return "NOT_TIME", before_txt, after_txt

        msgs = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('[role="alert"], .alert, .toast, [class*="toast"], [class*="Toast"]')
                .forEach(m => { const r = m.getBoundingClientRect(); const t = (m.innerText||'').trim();
                                if (t && r.width > 0) out.push(t.slice(0, 200)); });
            return out;
        }""")
        log(f"[{slug}] 未见成功迹象，页面提示: {msgs}，API响应: {(LAST_VOTE_RESP.get('body') or '')[:200]}")
        page.screenshot(path=f"vote_no_change_{slug}.png")
        return False, before_txt, after_txt
    except Exception as e:
        log(f"[{slug}] 投票异常: {e}")
        try:
            page.screenshot(path=f"vote_error_{slug}.png")
        except Exception:
            pass
        return False, "未知", "未知"

# ==================== 主流程 ====================
def main():
    servers = _parse_servers(SERVERS_RAW)
    if not servers:
        log("未配置服务器 (G4F_SERVERS)，退出")
        log("示例: G4F_SERVERS=/servers/fun 或 /servers/fun,/servers/other")
        sys.exit(1)

    ign = IGN or random_ign()
    log(f"目标服务器: {[s['slug'] for s in servers]}")
    log(f"投票昵称: {ign}")

    with sync_playwright() as p:
        browser = None
        try:
            if IS_PROXY:
                log(f"代理已启用: {PROXY_SERVER}")
            current_ip = get_current_ip()
            log(f"当前出口IP: {current_ip}")

            browser, page = open_browser(p)
            page.set_default_timeout(20000)
            _attach_vote_listener(page)

            if current_ip == "获取失败":
                try:
                    page.goto("https://api.ip.sb/ip", wait_until="domcontentloaded", timeout=20000)
                    t = page.locator("body").inner_text().strip()
                    if t and len(t) < 50:
                        current_ip = t
                        log(f"当前出口IP(浏览器): {current_ip}")
                except Exception:
                    pass

            results = []
            for svc in servers:
                # 单次运行可连续投多次(每次 +30 分钟, 冷却约 5 分钟)
                last = ("NOT_TIME", "未知", "未知")
                for n in range(max(1, VOTES_PER_RUN)):
                    result, before, after = vote_server(page, svc, ign)
                    last = (result, before, after)
                    if result is not True:
                        break
                    if n < VOTES_PER_RUN - 1:
                        log(f"等待 {VOTE_INTERVAL}s 冷却后继续投票（{n + 2}/{VOTES_PER_RUN}）...")
                        time.sleep(VOTE_INTERVAL)
                results.append((svc["slug"],) + last)

            has_fail = any(r[1] is False for r in results)
            for slug, result, before, after in results:
                icon, label = _status_icon(result)
                log(f"{icon} {slug}: {label}（{before} -> {after}）")
            send_telegram_notification(results, current_ip, ign)

            sys.exit(1 if has_fail else 0)
        except Exception as e:
            log(f"运行出错: {e}")
            sys.exit(1)
        finally:
            if browser:
                try:
                    browser.close()
                except Exception:
                    pass

if __name__ == "__main__":
    main()
