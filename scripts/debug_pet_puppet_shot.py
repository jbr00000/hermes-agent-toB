"""牛来分层木偶视觉验证：逐状态截图 + 走动过程连拍。

登录后打开桌宠设置面板，对六个状态预览逐个截图；
再静置等浮动桌宠进入走动，连拍 3 帧看摆臂。
截图输出到 tmp_pet_puppet/。
"""
from __future__ import annotations

import time
from pathlib import Path

import jwt
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp_pet_puppet"
STATES = ["idle", "thinking", "working", "confused", "eureka", "sad"]


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


TOKEN = mint_token()
USER_JSON = (
    '{"id":"85e490f0-1c3b-4d77-9e81-4ef49428f5bb","username":"admin","role":"superadmin",'
    '"status":"active","features":{"agent":true,"chat":true,"knowledge":true,"memory":true},'
    '"must_change_password":false,"created_at":1786000384.0}'
)


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page(viewport={"width": 1400, "height": 900})
        page.on("pageerror", lambda e: print("pageerror:", e))
        page.add_init_script(
            """
            localStorage.setItem('hermes-pet-visible', 'true');
            localStorage.setItem('hermes-pet-skin', '"niulai"');
            localStorage.setItem('hermes-pet-size', '96');
            """
        )
        page.route(
            "**/api/auth/login",
            lambda r: r.fulfill(
                status=200,
                content_type="application/json",
                body='{"access_token":"' + TOKEN + '","token_type":"bearer","user":' + USER_JSON + "}",
            ),
        )
        page.goto(BASE)
        page.wait_for_load_state("networkidle")
        page.wait_for_timeout(2000)
        page.fill('input[autocomplete="username"]', "admin")
        page.fill('input[autocomplete="current-password"]', "whatever")
        page.click('button[type="submit"]')
        page.wait_for_timeout(3000)

        # 新建 chat 会话让浮动桌宠出现
        btn = page.query_selector('button:has-text("新建任务")')
        if btn:
            btn.click()
            page.wait_for_timeout(1500)

        # 打开桌宠设置（顶栏 Cat 图标按钮），逐状态截图预览区
        settings_btn = page.query_selector('button[title*="桌宠"], button:has(svg.lucide-cat)')
        print("settings button:", settings_btn is not None)
        if settings_btn:
            settings_btn.click()
            page.wait_for_timeout(600)
            for i, state in enumerate(STATES):
                chips = page.query_selector_all('button:has-text("' + state + '")')
                # 状态预览 chips 的文案是中文标签，按顺序点更稳
                chip = page.query_selector_all(".flex.flex-wrap.gap-1 button")
                if chip and len(chip) > i:
                    chip[i].click()
                    page.wait_for_timeout(700)
                    preview = page.query_selector(".niulai")
                    if preview:
                        # 放大预览容器看清接缝（图层 CSS 是 100% 宽高，随容器缩放）
                        preview.evaluate("el => { el.style.width='160px'; el.style.height='286px' }")
                        page.wait_for_timeout(300)
                        preview.screenshot(path=str(OUT / f"state_{state}.png"))
                        print(f"state {state}: shot")
                    else:
                        print(f"state {state}: .niulai not found in preview")
            settings_btn.click()  # 关面板
            page.wait_for_timeout(400)

        # 走动连拍：等 pet-walking 出现，连拍 3 帧
        pet = page.query_selector('div[title*="可拖拽"]')
        print("float pet:", pet is not None)
        if pet:
            print("waiting for walk...")
            for _ in range(90):
                cls = pet.get_attribute("class") or ""
                if "pet-walking" in cls:
                    print("walking! shooting frames")
                    for f in range(3):
                        pet.screenshot(path=str(OUT / f"walk_{f}.png"))
                        page.wait_for_timeout(160)
                    break
                page.wait_for_timeout(500)
            else:
                print("no walk observed in 45s")

        browser.close()


if __name__ == "__main__":
    main()
