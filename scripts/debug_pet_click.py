"""用真实浏览器验证桌宠点击/走动/瞌睡行为：登录 → 找到浮动桌宠 → 点击 → 检查动画。

判定方式：
1. instrument：给 Element.prototype.animate 打猴子补丁，记录调用次数与元素。
2. 同时监听 console/pageerror，排除 JS 报错。
3. 点击后 dump 桌宠元素及其子树的计算样式/动画列表。

用法: python scripts/debug_pet_click.py [skin] [wait_seconds]
  skin: niulai（默认）或 cat；wait_seconds: 登录后静置观察自主行为的秒数（默认 0）
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

import jwt
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
USER = "admin"
PWD = "whatever"  # 登录接口被拦载，换成服务端 jwt.key 自签的 token
ROOT = Path(__file__).resolve().parent.parent


def mint_token() -> str:
    secret = (ROOT / ".hermes-dev" / "jwt.key").read_text(encoding="utf-8").strip()
    now = int(time.time())
    return jwt.encode(
        {
            "sub": "85e490f0-1c3b-4d77-9e81-4ef49428f5bb",
            "username": "admin",
            "role": "superadmin",
            "iat": now,
            "exp": now + 3600,
        },
        secret,
        algorithm="HS256",
    )


MINTED_TOKEN = mint_token()
USER_JSON = (
    '{"id":"85e490f0-1c3b-4d77-9e81-4ef49428f5bb","username":"admin","role":"superadmin",'
    '"status":"active","features":{"agent":true,"chat":true,"knowledge":true,"memory":true},'
    '"must_change_password":false,"created_at":1786000384.0}'
)


def main() -> None:
    skin = sys.argv[1] if len(sys.argv) > 1 else "niulai"
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})

        errors: list[str] = []
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.on("console", lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None)

        # 打点：记录 animate 调用；并在 app JS 运行前预设桌宠偏好
        init_js = """
            localStorage.setItem('hermes-pet-visible', 'true');
            localStorage.setItem('hermes-pet-skin', '"__SKIN__"');
            window.__animateCalls = [];
            const orig = Element.prototype.animate;
            Element.prototype.animate = function (...args) {
              window.__animateCalls.push({
                tag: this.tagName, cls: this.className?.baseVal ?? String(this.className),
                t: performance.now(),
              });
              return orig.apply(this, args);
            };
            """
        page.add_init_script(init_js.replace("__SKIN__", skin))

        # 拦截登录接口，返回自签 token（后端会正常验签通过）
        def fake_login(route):
            print(">> login intercepted")
            route.fulfill(
                status=200,
                content_type="application/json",
                body='{"access_token":"' + MINTED_TOKEN + '","token_type":"bearer","user":' + USER_JSON + "}",
            )

        page.route("**/api/auth/login", fake_login)
        page.on("request", lambda r: print(">> req:", r.method, r.url) if "/api/" in r.url else None)
        page.on("response", lambda r: print(">> resp:", r.status, r.url) if "/api/" in r.url else None)

        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)  # 等首屏 refreshSession 结算完，避免表单被重挂载清掉

        # 登录
        page.fill('input[autocomplete="username"]', USER)
        page.fill('input[autocomplete="current-password"]', PWD)
        filled = page.eval_on_selector('input[autocomplete="current-password"]', "el => el.value")
        print("password field value:", filled)
        page.click('button[type="submit"]')
        page.wait_for_timeout(3000)
        err = page.query_selector('[role="alert"]')
        if err:
            print("login error shown:", err.inner_text())

        # 确保桌宠显示且形象为牛来（已在 add_init_script 里预设 localStorage）
        # 新建一个 chat 会话让浮动桌宠出现（无标签页时 PetFloat 不渲染）
        new_btn = page.query_selector('button:has-text("新建任务")')
        if new_btn:
            new_btn.click()
            page.wait_for_timeout(1500)

        pet = page.query_selector('div[title*="可拖拽"]')
        print("pet element found:", pet is not None)
        if not pet:
            import pathlib
            out = pathlib.Path(__file__).parent.parent / "tmp_pet_debug"
            out.mkdir(exist_ok=True)
            page.screenshot(path=str(out / "after_login.png"), full_page=False)
            body_text = page.evaluate("document.body.innerText.slice(0, 500)")
            print("body text:", body_text)
            titles = page.evaluate("Array.from(document.querySelectorAll('[title]')).map(e => e.getAttribute('title'))")
            print("all title attrs:", titles)
            browser.close()
            return

        print("pet title:", pet.get_attribute("title"))
        box = pet.bounding_box()
        print("pet bounding box:", box)
        html = pet.evaluate("el => el.outerHTML.slice(0, 400)")
        print("pet html:", html)

        # 检查子元素 animate 是否可用 & 当前是否有动画在跑
        info = pet.evaluate(
            """el => {
              const child = el.firstElementChild;
              return {
                childTag: child?.tagName,
                childCls: child?.className?.baseVal ?? String(child?.className),
                hasAnimate: typeof child?.animate === 'function',
                runningAnims: child?.getAnimations({subtree: true}).map(a => ({
                  name: a.animationName ?? a.constructor.name,
                  playState: a.playState,
                })),
                reducedMotion: matchMedia('(prefers-reduced-motion: reduce)').matches,
              };
            }"""
        )
        print("child info:", info)

        # 真实点击（pointerdown/up 在元素中心）
        cx = box["x"] + box["width"] / 2
        cy = box["y"] + box["height"] / 2
        page.mouse.move(cx, cy)
        page.mouse.down()
        page.mouse.up()
        page.wait_for_timeout(200)

        calls = page.evaluate("window.__animateCalls")
        print("animate calls after click:", calls)

        # 点击后立即看子树动画
        anims = pet.evaluate(
            "el => el.firstElementChild?.getAnimations({subtree: false}).map(a => ({cs: a.constructor.name, ps: a.playState, ct: a.currentTime}))"
        )
        print("child animations right after click:", anims)

        # 连点 3 次：每次都应触发
        for i in range(3):
            page.evaluate("window.__animateCalls = []")
            page.mouse.down()
            page.mouse.up()
            page.wait_for_timeout(150)
            calls = page.evaluate("window.__animateCalls.length")
            print(f"rapid click {i + 1}: animate calls = {calls}")

        # 双击：300ms 内两次点击应只播一次「双击反应」（双击动画时长 900ms）
        page.evaluate("window.__animateCalls = []")
        page.mouse.down(); page.mouse.up()
        page.wait_for_timeout(80)
        page.mouse.down(); page.mouse.up()
        page.wait_for_timeout(400)
        calls = page.evaluate("window.__animateCalls")
        print("double click: animate calls =", len(calls), "(期望 1 次双击反应)")

        # 自主走动：静置 30s，每 500ms 采样位置与 class（35% 概率/5s，30s 内大概率出现）
        print("watching for autonomous walk (30s)...")
        last_box = pet.bounding_box()
        walked = False
        for _ in range(60):
            page.wait_for_timeout(500)
            cls = pet.get_attribute("class") or ""
            box = pet.bounding_box()
            if "pet-walking" in cls or (box and last_box and abs(box["x"] - last_box["x"]) > 2):
                walked = True
                print(f"  walk detected: class={cls!r} x={box and box['x']:.1f}")
                break
            last_box = box
        print("autonomous walk observed:", walked)

        # 瞌睡：快进 Date.now +120s → 下一个 5s 节拍应进入 SLEEP（pet-sleeping + Zzz）
        page.evaluate(
            """() => {
              const real = Date.now.bind(Date);
              Date.now = () => real() + 120000;
            }"""
        )
        page.wait_for_timeout(6000)
        cls = pet.get_attribute("class") or ""
        zzz = page.query_selector(".pet-zzz")
        print("sleeping class:", "pet-sleeping" in cls, "| zzz overlay:", zzz is not None)

        # 点击唤醒（桌宠可能已走离原位，重新取中心点）
        box = pet.bounding_box()
        page.mouse.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        page.mouse.down(); page.mouse.up()
        page.wait_for_timeout(300)
        cls = pet.get_attribute("class") or ""
        print("after wake click, sleeping class:", "pet-sleeping" in cls)

        if errors:
            print("ERRORS:")
            for e in errors[:10]:
                print(" ", e)

        browser.close()


if __name__ == "__main__":
    main()
