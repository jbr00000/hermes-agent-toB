"""一次性素材处理脚本：牛来桌宠.png → 去水印 + 抠图 → 透明立绘 PNG。

步骤（顺序有讲究）：
1. 先在原图上 inpaint 左下「Baidu百科」白色水印（此时它同时压在背景和牛身上，
   近白低饱和像素 + 膨胀掩码 + TELEA 修复；背景部分修得好坏无所谓，反正要抠掉）。
2. rembg(u2net) 抠掉森林背景。
3. alpha 拉伸：抠图主体（牛皮）常被判成半透明，把 alpha 重新映射成接近二值，
   保住实心身体、只留边缘过渡。
4. 按 alpha 裁边 + 缩放到 512px 边长内，输出到 apps/web 资源目录。

运行（仓库根目录）：
    /d/Anaconda/envs/hermes/python.exe scripts/build_pet_asset.py
"""
from __future__ import annotations

import io
from pathlib import Path

import cv2
import numpy as np
from PIL import Image
from rembg import new_session, remove

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "docs" / "牛来桌宠.png"
OUT_DIR = ROOT / "apps" / "web" / "src" / "components" / "pet" / "assets"
OUT = OUT_DIR / "niulai.png"
# 桌宠最大展示 96px，2x 高分屏冗余到 256px 足够（512 会让包体多 ~200KB）
MAX_SIDE = 256

# 水印所在区域（相对原图比例）：左下角
WM_X_END = 0.30
WM_Y_START = 0.76


def inpaint_watermark(img: Image.Image) -> Image.Image:
    """抠图后修复左下「Baidu百科」水印残字：背景上的部分已随抠图消失，
    只需处理落在牛身上的近白低饱和像素（且必须是不透明前景）。"""
    arr = np.array(img)
    bgr = arr[:, :, :3]
    h, w = bgr.shape[:2]
    hsv = cv2.cvtColor(bgr, cv2.COLOR_RGB2HSV)
    region = np.zeros((h, w), dtype=bool)
    region[int(h * WM_Y_START):, : int(w * WM_X_END)] = True
    near_white = (hsv[:, :, 2] > 150) & (hsv[:, :, 1] < 90)
    mask = (region & near_white & (arr[:, :, 3] > 200)).astype(np.uint8) * 255
    mask = cv2.dilate(mask, np.ones((5, 5), np.uint8), iterations=2)
    repaired = cv2.inpaint(bgr, mask, inpaintRadius=6, flags=cv2.INPAINT_TELEA)
    arr[:, :, :3] = repaired
    return Image.fromarray(arr, "RGBA")


def cutout(img: Image.Image) -> Image.Image:
    """u2net + alpha matting：主体形状（尤其阴影里的手臂）保留得最好，
    但身体落在「不确定区」只给半透明 alpha。所以只用 matting 结果取剪影形状：
    阈值化 → 闭运算补缝 → 填洞 → 最大连通域 → 边缘羽化后重新作为 alpha，
    颜色直接用原图 RGB。"""
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
    soft = np.array(Image.open(io.BytesIO(result)))[:, :, 3]

    mask = (soft > 30).astype(np.uint8) * 255
    kernel = np.ones((5, 5), np.uint8)
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, kernel, iterations=3)

    # 填洞：从边界 floodfill 背景，未被淹到的 0 区域都是内部洞
    flood = mask.copy()
    flood_mask = np.zeros((flood.shape[0] + 2, flood.shape[1] + 2), np.uint8)
    cv2.floodFill(flood, flood_mask, (0, 0), 128)
    mask[flood == 0] = 255

    count, labels, stats, _ = cv2.connectedComponentsWithStats(
        (mask > 0).astype(np.uint8), connectivity=8
    )
    if count > 1:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask[labels != largest] = 0

    mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=2)  # 收掉边缘背景残留
    mask = cv2.GaussianBlur(mask, (5, 5), 0)  # 边缘羽化

    arr = np.dstack([np.array(img.convert("RGB")), mask])
    return Image.fromarray(arr, "RGBA")


def trim_and_scale(img: Image.Image) -> Image.Image:
    alpha = np.array(img)[:, :, 3]
    ys, xs = np.where(alpha > 8)
    if len(xs) == 0:
        raise RuntimeError("抠图结果为空")
    box = (xs.min(), ys.min(), xs.max() + 1, ys.max() + 1)
    img = img.crop(box)
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return img


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = Image.open(SRC)
    img = cutout(img)  # 必须在原图上抠：先 inpaint 会把 u2net 的身体分割带崩
    img = inpaint_watermark(img)
    img = trim_and_scale(img)
    img.save(OUT)
    print(f"OK {OUT} {img.size}")


if __name__ == "__main__":
    main()
