"""拖拽挣扎/放下松气验证：拎起桌宠 → 检查挣扎动画 → 放下 → 检查 relief WAAPI。

截图 tmp_pet_puppet/drag_*.png 供肉眼核对四肢扑腾姿态。
"""
from __future__ import annotations

import time
from pathlib import Path

import jwt
from playwright.sync_api import sync_playwright

BASE = "http://localhost:5173"
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "tmp_pet_puppet"


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
            window.__animateCalls = [];
            const orig = Element.prototype.animate;
            Element.prototype.animate = function (...args) {
              window.__animateCalls.push({ cls: String(this.className), t: performance.now() });
              return orig.apply(this, args);
            };
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
        page.wait_for_timeout(2500)
        btn = page.query_selector('button:has-text("新建任务")')
        if btn:
            btn.click()
            page.wait_for_timeout(1500)

        pet = page.query_selector('div[title*="可拖拽"]')
        box = pet.bounding_box()
        cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2

        # 拎起并拖动
        page.mouse.move(cx, cy)
        page.mouse.down()
        for i in range(1, 9):
            page.mouse.move(cx + i * 12, cy - i * 6)
            page.wait_for_timeout(40)
        page.wait_for_timeout(200)

        cls = pet.get_attribute("class") or ""
        print("dragging class:", "pet-dragging" in cls)
        anims = page.evaluate(
            """document.querySelector('div[title*="可拖拽"] .pet-stage')
              .getAnimations({subtree: true}).map(a => a.animationName ?? a.constructor.name)"""
        )
        print("running anims while dragging:", anims)
        pet.screenshot(path=str(OUT / "drag_struggle.png"))

        # 放下 → relief（pet-relief 类驱动的一次性多层动画，~0.95s）
        page.mouse.up()
        page.wait_for_timeout(150)
        cls = pet.get_attribute("class") or ""
        print("relief class after release:", "pet-relief" in cls)
        anims = page.evaluate(
            """document.querySelector('div[title*="可拖拽"] .pet-stage')
              .getAnimations({subtree: true}).map(a => a.animationName ?? a.constructor.name)"""
        )
        print("running anims during relief:", anims)
        pet.screenshot(path=str(OUT / "drag_relief.png"))
        page.wait_for_timeout(1000)
        cls = pet.get_attribute("class") or ""
        print("relief class cleared after ~1.1s:", "pet-relief" not in cls)

        browser.close()


if __name__ == "__main__":
    main()
