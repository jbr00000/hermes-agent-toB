"""调试分层：输出 soft alpha 的行宽剖面 + 各部位缝隙行数，辅助调 build_pet_puppet 参数。"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "牛来桌宠.png"
MAX_SIDE = 256


def main() -> None:
    img = Image.open(SRC)
    session = new_session("u2net")
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    result = remove(
        buf.getvalue(),
        session=session,
        alpha_matting=True,
        alpha_matting_foreground_threshold=220,
        alpha_matting_background_threshold=15,
        alpha_matting_erode_size=8,
    )
    rgba = np.array(Image.open(io.BytesIO(result)))
    rgb, soft = rgba[:, :, :3], rgba[:, :, 3]

    # 简化剪影：阈值 + 最大连通域（不做闭运算/填洞，保留手臂与躯干间的真缝隙）
    mask = (soft > 30).astype(np.uint8) * 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
    mask[labels != largest] = 0

    # trim + scale（与正式脚本一致）
    ys, xs = np.where(mask > 8)
    box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    img2 = Image.fromarray(np.dstack([rgb, mask]), "RGBA").crop(box)
    img2.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    arr = np.array(img2)
    alpha = arr[:, :, 3]
    h, w = alpha.shape
    print(f"trimmed {w}x{h}")

    width = (alpha > 128).sum(axis=1)
    for y in range(0, h, 4):
        bar = "#" * (width[y] // 2)
        print(f"{y:3d} {y / h:5.1%} {width[y]:3d} {bar}")

    # 手臂缝隙（左/右）：肩→75% 波段逐行
    print("\narm gaps (threshold>64, gap>=3):")
    for y in range(int(h * 0.30), int(h * 0.75), 3):
        row = alpha[y] > 64
        runs = []
        s = -1
        for x, on in enumerate(row):
            if on and s < 0:
                s = x
            elif not on and s >= 0:
                runs.append((s, x))
                s = -1
        if s >= 0:
            runs.append((s, w))
        mid = w // 2
        note = ""
        lruns = [r for r in runs if r[0] < mid]
        rruns = [r for r in runs if r[1] > mid]
        if len(lruns) >= 2 and lruns[1][0] - lruns[0][1] >= 3:
            note += f" L-gap@{lruns[0][1]}"
        if len(rruns) >= 2 and rruns[-1][0] - rruns[-2][1] >= 3:
            note += f" R-gap@{rruns[-1][0]}"
        if note:
            print(f"  y={y} ({y / h:.1%}){note}  runs={runs}")

    Image.fromarray(arr, "RGBA").save(ROOT / "tmp_puppet_debug_mask.png")


if __name__ == "__main__":
    main()
