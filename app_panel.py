#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Gaming4Free 面板续期脚本（Cookie 版，+90 分钟/次）

与 app.py 的区别：
  app.py        走公开投票页 /servers/<slug>，一次 +30 分钟，无需登录
  本脚本        走登录后的控制面板 /server/<id>/console，点 "+ 90 min"，一次 +90 分钟

面板续期流程（已实测确认 2026-11-04）：
  1. 带登录 cookie 打开 /server/<id>/console
  2. 页面显示剩余时间（如 "21:08:06 remaining"）与两个按钮：
       + 90 min        免费（Alpine.js, @click="extendClicked()"）
       +24h $0.15      付费（Livewire, wire:click="extendPaid"，余额不足时显示 "+24h (no bal)"）
  3. 点 "+ 90 min" -> 弹出 "Verify you're human to continue" Turnstile
  4. 通过验证 -> 剩余时间 +90 分钟（实测 21:08:06 -> 22:37:49）

配置(环境变量):
  G4F_COOKIE   登录 cookie(pelican_session)，必填
  G4F_SERVERS  只续指定服务器(identifier 或名称)，逗号分隔；留空则续全部
  TG_BOT_TOKEN / TG_CHAT_ID  可选 Telegram 通知
  IS_PROXY / PROXY_SERVER    代理
"""

import os, sys, time, json

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import app as A
from app import (log, open_browser, handle_cloudflare, solve_turnstile,
                 apply_cookies, _check_login, COOKIE_RAW, CONTROL_URL,
                 get_current_ip, send_telegram_notification, _status_icon,
                 sync_playwright)

ONLY = [s.strip() for s in (os.environ.get('G4F_SERVERS') or "").split(",") if s.strip()]


def remaining(page):
    """读取剩余时间 HH:MM:SS"""
    try:
        return page.evaluate("""() => {
            const m = document.body.innerText.match(/([\\d]{1,3}:[\\d]{2}:[\\d]{2})\\s*remaining/i);
            return m ? m[1] : null;
        }""")
    except Exception:
        return None


def to_secs(hms):
    if not hms:
        return None
    try:
        p = [int(x) for x in hms.split(":")]
        while len(p) < 3:
            p.insert(0, 0)
        return p[0] * 3600 + p[1] * 60 + p[2]
    except Exception:
        return None


def get_servers(page):
    """拿服务器列表: 优先页面内 fetch(带 cookie 和浏览器指纹), 失败则从页面链接提取"""
    try:
        d = page.evaluate("""async () => {
            try {
                const r = await fetch('/api/client', {headers: {'Accept': 'application/json'}});
                if (!r.ok) return {__err: r.status};
                return await r.json();
            } catch (e) { return {__err: String(e).slice(0, 80)}; }
        }""")
        if not d.get("__err"):
            out = []
            for item in d.get("data", []):
                a = item.get("attributes", {})
                rn = a.get("renewal") or {}
                out.append({
                    "id": a.get("identifier"),
                    "name": a.get("name") or a.get("identifier"),
                    "seconds": rn.get("seconds_remaining"),
                    "expires_at": rn.get("expires_at"),
                })
            if out:
                return out
        else:
            log(f"页面内 API 返回 {d['__err']}，改用页面链接提取")
    except Exception as e:
        log(f"页面内 API 异常: {e}")

    # 兜底: 从首页链接提取 /server/<id>/console
    try:
        page.goto(CONTROL_URL + "/", wait_until="domcontentloaded", timeout=60000)
        time.sleep(6)
        found = page.evaluate("""() => {
            const out = [];
            document.querySelectorAll('a[href*="/server/"]').forEach(a => {
                const h = a.getAttribute('href') || '';
                const parts = h.split('/');
                const i = parts.indexOf('server');
                const sid = (i >= 0 && parts[i + 1]) ? parts[i + 1] : null;
                const t = (a.innerText || '').trim().split('\n')[0];
                if (sid && !out.some(x => x.id === sid))
                    out.push({id: sid, name: t || sid});
            });
            return out;
        }""")
        for x in found:
            x["seconds"] = None
            x["expires_at"] = None
        return found
    except Exception as e:
        log(f"链接提取异常: {e}")
        return []


def renew_server(page, srv):
    """面板续期单台服务器。返回 (结果, 前剩余, 后剩余)"""
    sid, name = srv["id"], srv["name"]
    url = f"{CONTROL_URL}/server/{sid}/console"
    try:
        log(f"[{name}] 打开控制台...")
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        handle_cloudflare(page)
        time.sleep(8)

        before = remaining(page)
        log(f"[{name}] 续期前剩余: {before}")

        btn = page.locator('.rt-btn-free').first
        if btn.count() == 0:
            log(f"[{name}] 未找到 +90min 按钮")
            page.screenshot(path=f"renew_no_btn_{sid}.png")
            return False, before or "未知", before or "未知"

        try:
            if btn.is_disabled():
                log(f"[{name}] +90min 按钮当前禁用（冷却中），跳过")
                return "NOT_TIME", before or "未知", before or "未知"
        except Exception:
            pass

        # 冷却中按钮文本形如 "04:55 cd"
        btn_text = page.evaluate("""() => {
            const b = document.querySelector('.rt-btn-free');
            return b ? b.innerText.trim() : null;
        }""")
        log(f"[{name}] 按钮文本: {btn_text!r}")
        if btn_text and 'cd' in btn_text.lower():
            log(f"[{name}] 冷却中（{btn_text}），跳过")
            return "NOT_TIME", before or "未知", before or "未知"

        # 读取续期按钮的 Alpine 状态(冷却/广告状态)
        st = page.evaluate("""() => {
            const el = document.querySelector('.rt-btn-free');
            if (!el) return null;
            const d = (el._x_dataStack && el._x_dataStack[0]) || null;
            if (!d) return {noData: true};
            return {
                extendDisabled: d.extendDisabled ?? null,
                adLoading: d.adLoading ?? null,
                adWaiting: d.adWaiting ?? null,
                adCooldown: d.adCooldown ?? null,
                isNativeApp: d.isNativeApp ?? null,
                expiresTimestamp: d.expiresTimestamp ?? null
            };
        }""")
        log(f"[{name}] 按钮状态: {json.dumps(st, ensure_ascii=False) if st else 'null'}")
        if st and st.get("extendDisabled") is True and not st.get("isNativeApp"):
            log(f"[{name}] 续期按钮处于冷却状态，跳过")
            return "NOT_TIME", before or "未知", before or "未知"

        # 控制台页同样有广告拦截遮罩, 会拦截点击
        A._dismiss_adblock_overlay(page)
        time.sleep(1)

        log(f"[{name}] 点击 +90 min ...")
        clicked = False
        try:
            page.evaluate("""() => { const b = document.querySelector('.rt-btn-free'); if (b) b.click(); }""")
            clicked = True
        except Exception:
            pass
        if not clicked:
            try:
                btn.click(timeout=8000)
                clicked = True
            except Exception:
                log(f"[{name}] 按钮点击失败")
                return False, before or "未知", before or "未知"
        time.sleep(4)

        # 等验证弹窗出现
        appeared = False
        for _ in range(8):
            ok = page.evaluate("""() => /verify you.re human/i.test(document.body.innerText)""")
            if ok:
                appeared = True
                break
            time.sleep(1)
        log(f"[{name}] 验证弹窗: {'已出现' if appeared else '未出现（可能直接续期）'}")

        if appeared:
            if not solve_turnstile(page, timeout=200):
                log(f"[{name}] Turnstile 未通过")
                page.screenshot(path=f"renew_captcha_fail_{sid}.png")
                return False, before or "未知", before or "未知"

        log(f"[{name}] 等待续期生效...")
        after = before
        cd_shown = False
        for _ in range(20):
            time.sleep(3)
            st2 = page.evaluate("""() => {
                const b = document.querySelector('.rt-btn-free');
                const m = document.body.innerText.match(/([\\d]{1,3}:[\\d]{2}:[\\d]{2})\\s*remaining/i);
                return {btn: b ? b.innerText.trim() : null, rem: m ? m[1] : null};
            }""")
            after = st2.get("rem") or after
            if st2.get("btn") and 'cd' in st2["btn"].lower():
                cd_shown = True   # 按钮进入冷却 = 本次续期已被接受
                log(f"[{name}] 按钮进入冷却: {st2['btn']}")
                break
            a, b = to_secs(after), to_secs(before)
            if a is not None and b is not None and a > b + 60:
                break

        b_sec, a_sec = to_secs(before), to_secs(after)
        if cd_shown or (b_sec is not None and a_sec is not None and a_sec > b_sec + 60):
            gain = (a_sec - b_sec) / 60
            log(f"[{name}] 续期成功! {before} -> {after}（+{gain:.0f} 分钟）")
            return True, before, after

        log(f"[{name}] 剩余未增加: {before} -> {after}")
        page.screenshot(path=f"renew_no_change_{sid}.png")
        return False, before or "未知", after or "未知"
    except Exception as e:
        log(f"[{name}] 续期异常: {e}")
        try:
            page.screenshot(path=f"renew_error_{sid}.png")
        except Exception:
            pass
        return False, "未知", "未知"


def main():
    if not COOKIE_RAW:
        log("缺少 G4F_COOKIE（登录 cookie），本脚本必须提供登录态")
        sys.exit(1)

    log(f"模式: 面板续期（+90 分钟/次）")
    with sync_playwright() as p:
        browser = None
        try:
            if A.IS_PROXY:
                log(f"代理已启用: {A.PROXY_SERVER}")
            current_ip = get_current_ip()
            log(f"当前出口IP: {current_ip}")

            browser, page = open_browser(p)
            page.set_default_timeout(30000)

            apply_cookies(page.context)
            if not _check_login(page):
                log("登录 cookie 无效，退出（请更新 G4F_COOKIE）")
                sys.exit(1)

            servers = get_servers(page)
            if not servers:
                log("未获取到服务器列表")
                sys.exit(1)
            log(f"账号下服务器 {len(servers)} 台")

            if ONLY:
                picked = [s for s in servers
                          if s["id"] in ONLY or s["name"] in ONLY]
                if picked:
                    servers = picked
                else:
                    log(f"G4F_SERVERS 指定的 {ONLY} 未匹配到，改为全部")

            results = []
            for srv in servers:
                log(f"--- {srv['name']} ({srv['id']}) 当前 "
                    f"{(srv['seconds'] or 0)/3600:.2f}h ---")
                result, before, after = renew_server(page, srv)
                results.append((srv["name"], result, before, after))

            has_fail = any(r[1] is False for r in results)
            for name, result, before, after in results:
                if result is True:
                    label = "续期成功"
                elif result == "NOT_TIME":
                    label = "冷却中/无需续期"
                else:
                    label = "续期失败"
                log(f"{'✅' if result is True else ('🕐' if result == 'NOT_TIME' else '❌')} "
                    f"{name}: {label}（{before} -> {after}）")
            send_telegram_notification(results, current_ip, "")
            sys.exit(1 if has_fail else 0)
        except SystemExit:
            raise
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
