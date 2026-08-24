"""牛来桌宠「分层木偶」素材生成：docs/牛来桌宠.png → 身体/头/左臂/右臂 四个透明图层。

在 build_pet_asset.py（整图立绘）的基础上更进一步，把各部位拆成独立图层，
前端 NiulaiAvatar 按层叠加渲染，每层独立做 CSS 变换（歪头、摆臂、垂臂），
动画比整图晃动精细得多。各图层保持同一画布尺寸，绝对定位 inset:0 即对齐。

分割策略（针对这张正面直立全身像，一次性脚本，参数按视觉校验调定）：
- 剪影：u2net + alpha matting 的 soft alpha 直接阈值化 + 最大连通域。
  不做闭运算/填洞——那会把手臂与躯干之间的背景缝隙糊死，导致无法拆臂。
- 头：在 NECK_Y_FRAC（颈部）横切，头图层向下带羽化重叠，转头不露头身缝。
- 手臂：肘以下有真实背景缝隙（逐行检测）；肘以上与躯干粘连，切线从肩部枢轴
  直线插值到缝隙首行。手臂从身体抠除后，主体 inpaint 补上缺口（摆臂时不露透明洞）。
- 输出 4 个全画布 PNG + niulai_puppet_meta.ts（各层 transform-origin 枢轴，百分比）
  + _puppet_check.png（静止合成校验）+ _puppet_motion.png（各层旋转后的合成抽帧，
  用来肉眼检查枢轴/接缝是否穿帮）。

运行（仓库根目录）：
    /d/Anaconda/envs/hermes/python.exe scripts/build_pet_puppet.py
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
MAX_SIDE = 256

ALPHA_THR = 64  # 分割用的实体阈值（剪影 alpha 0-255）
NECK_Y_FRAC = 0.37  # 头/颈横切位置（图高比例）：颌底与肩膀之间的颈部
SHOULDER_Y_FRAC = 0.42  # 肩部枢轴高度
ARM_PIVOT_X_FRAC = 0.21  # 肩枢轴距同侧轮廓的水平位置（图宽比例，左臂用，右臂镜像）
MIN_GAP_ROWS = 10  # 缝隙行少于此值放弃拆臂（手臂贴身，留在身体上）
LEG_HIP_Y_FRAC = 0.60  # 腿/躯干横切位置（胯部）
LEG_PIVOT_X_FRAC = {"legL": 0.38, "legR": 0.62}  # 髋部枢轴（图宽比例）


# ---------------------------------------------------------------- 抠图

def cutout(img: Image.Image) -> Image.Image:
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

    mask = (soft > 30).astype(np.uint8) * 255
    count, labels, stats, _ = cv2.connectedComponentsWithStats((mask > 0).astype(np.uint8), 8)
    if count > 1:
        largest = 1 + int(np.argmax(stats[1:, cv2.CC_STAT_AREA]))
        mask[labels != largest] = 0
    mask = cv2.erode(mask, np.ones((3, 3), np.uint8), iterations=1)
    mask = cv2.GaussianBlur(mask, (5, 5), 0)
    return Image.fromarray(np.dstack([rgb, mask]), "RGBA")


def trim_and_scale(img: Image.Image) -> Image.Image:
    alpha = np.array(img)[:, :, 3]
    ys, xs = np.where(alpha > 8)
    if len(xs) == 0:
        raise RuntimeError("抠图结果为空")
    img = img.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    img.thumbnail((MAX_SIDE, MAX_SIDE), Image.LANCZOS)
    return img


# ---------------------------------------------------------------- 几何分割

def row_runs(alpha_row: np.ndarray, thr: int = ALPHA_THR) -> list[tuple[int, int]]:
    """一行 alpha 的实体区段 [(start, end_exclusive), ...]。"""
    solid = alpha_row > thr
    runs: list[tuple[int, int]] = []
    start = -1
    for x, on in enumerate(solid):
        if on and start < 0:
            start = x
        elif not on and start >= 0:
            runs.append((start, x))
            start = -1
    if start >= 0:
        runs.append((start, len(solid)))
    return runs


def split_head(
    rgb: np.ndarray, alpha: np.ndarray
) -> tuple[np.ndarray, np.ndarray, tuple[float, float]]:
    """颈部横切：头图层带羽化重叠，身体删掉头。返回 (head_rgba, body_alpha, pivot)。"""
    h, w = alpha.shape
    cut = int(h * NECK_Y_FRAC)
    overlap = max(2, int(h * 0.03))

    head_a = alpha.astype(np.float64)
    ramp = np.linspace(1.0, 0.0, overlap + 1)
    for i in range(overlap + 1):
        if cut + i < h:
            head_a[cut + i] *= ramp[i]
    head_a[cut + overlap + 1:] = 0
    head_a = np.clip(head_a, 0, 255).astype(np.uint8)

    body_a = alpha.copy()
    body_a[: cut + 1] = 0

    # 枢轴：切线行实体段的中心（即颈部截面中心）
    runs = row_runs(alpha[cut])
    if runs:
        run = max(runs, key=lambda r: r[1] - r[0])
        pivot = ((run[0] + run[1]) / 2, float(cut))
    else:
        pivot = (w / 2, float(cut))
    return np.dstack([rgb, head_a]), body_a, pivot


def detect_arm_gaps(alpha: np.ndarray, side: str, band_top: int, band_bot: int) -> dict[int, int]:
    """波段内逐行找手臂与躯干间的背景缝隙，返回 {y: 缝隙靠臂侧的 x 坐标}。"""
    w = alpha.shape[1]
    mid = w // 2
    gaps: dict[int, int] = {}
    for y in range(band_top, band_bot):
        runs = row_runs(alpha[y])
        if side == "left":
            runs = [r for r in runs if r[0] < mid]
            if len(runs) >= 2 and runs[1][0] - runs[0][1] >= 3:
                gaps[y] = runs[0][1]
        else:
            runs = [r for r in runs if r[1] > mid]
            if len(runs) >= 2 and runs[-1][0] - runs[-2][1] >= 3:
                gaps[y] = runs[-1][0]
    return gaps


def split_arm(
    rgb: np.ndarray, alpha: np.ndarray, body_a: np.ndarray, side: str, gaps: dict[int, int]
) -> tuple[np.ndarray, np.ndarray, tuple[float, float]] | None:
    """肩部枢轴直线 + 肘下缝隙线把手臂切下。返回 (arm_rgba, body_alpha, pivot)。

    alpha 是完整剪影（手臂图层从它取像素）；body_a 是身体当前 alpha
    （已删掉头/先拆的臂），在此基础上继续减掉这条手臂。
    """
    h, w = alpha.shape
    ys = sorted(gaps)
    if len(ys) < MIN_GAP_ROWS:
        return None
    y0, y1 = ys[0], ys[-1]

    py = int(h * SHOULDER_Y_FRAC)
    px = w * ARM_PIVOT_X_FRAC if side == "left" else w * (1 - ARM_PIVOT_X_FRAC)
    # 缝隙末行以下垂直延伸一小段罩住手掌
    arm_bot = min(max(ys) + int(h * 0.03), int(h * 0.78))

    def cut_x(y: int) -> float:
        if y <= y0:
            t = (y - py) / max(1, y0 - py)
            return px + t * (gaps[y0] - px)
        if y <= y1:
            return float(gaps[min(ys, key=lambda k: abs(k - y))])
        return float(gaps[y1])

    mask = np.zeros((h, w), np.uint8)
    for y in range(py, arm_bot + 1):
        cx = int(round(cut_x(y)))
        if side == "left":
            mask[y, :cx] = 255
        else:
            mask[y, cx:] = 255
    mask = cv2.GaussianBlur(mask, (5, 5), 0)  # 切边羽化

    arm_a = (alpha.astype(np.float64) * (mask.astype(np.float64) / 255)).astype(np.uint8)
    new_body_a = (body_a.astype(np.float64) * (1 - mask.astype(np.float64) / 255)).astype(np.uint8)
    return np.dstack([rgb, arm_a]), new_body_a, (px, float(py))


def split_leg(
    rgb: np.ndarray, alpha: np.ndarray, body_a: np.ndarray, key: str, crotch_x: int
) -> tuple[np.ndarray, np.ndarray, tuple[float, float]]:
    """胯中线竖切 + 胯线横切拆出一条腿（正面直立像双腿粘连，直接对半竖切，
    静止时两层像素完全一致，切缝不可见）。返回 (leg_rgba, body_alpha, pivot)。"""
    h, w = alpha.shape
    y0 = int(h * LEG_HIP_Y_FRAC)
    mask = np.zeros((h, w), np.uint8)
    if key == "legL":
        mask[y0:, :crotch_x] = 255
    else:
        mask[y0:, crotch_x:] = 255
    mask = cv2.GaussianBlur(mask, (5, 5), 0)  # 切边羽化

    leg_a = (alpha.astype(np.float64) * (mask.astype(np.float64) / 255)).astype(np.uint8)
    new_body_a = (body_a.astype(np.float64) * (1 - mask.astype(np.float64) / 255)).astype(np.uint8)
    pivot = (w * LEG_PIVOT_X_FRAC[key], float(y0))
    return np.dstack([rgb, leg_a]), new_body_a, pivot


def inpaint_body(rgb: np.ndarray, body_a: np.ndarray, orig_alpha: np.ndarray) -> np.ndarray:
    """把身体被切掉的头/臂/腿区域 RGB 用周围纹理补上（部件摆开时露出的躯干不穿帮）。
    只对身体 alpha 基本掉光的区域动刀——羽化缝上的半透明带保留原始 RGB，
    否则接缝处会显出一条模糊线。"""
    hole = ((orig_alpha > ALPHA_THR) & (body_a <= 32)).astype(np.uint8) * 255
    if hole.sum() == 0:
        return rgb
    hole = cv2.dilate(hole, np.ones((7, 7), np.uint8), iterations=1)
    repaired = cv2.inpaint(rgb, hole, inpaintRadius=8, flags=cv2.INPAINT_TELEA)
    out = rgb.copy()
    out[hole > 0] = repaired[hole > 0]
    return out


# ---------------------------------------------------------------- 校验图

def rotate_layer(layer: np.ndarray, pivot: tuple[float, float], deg: float) -> np.ndarray:
    h, w = layer.shape[:2]
    mat = cv2.getRotationMatrix2D((pivot[0], pivot[1]), deg, 1.0)
    return cv2.warpAffine(layer, mat, (w, h), flags=cv2.INTER_LINEAR, borderValue=(0, 0, 0, 0))


def composite(layers: list[np.ndarray]) -> np.ndarray:
    h, w = layers[0].shape[:2]
    out = np.zeros((h, w, 4), np.float64)
    for layer in layers:
        la = layer[:, :, 3:4].astype(np.float64) / 255
        out[:, :, :3] = layer[:, :, :3] * la + out[:, :, :3] * (1 - la)
        out[:, :, 3:4] = np.maximum(out[:, :, 3:4], layer[:, :, 3:4])
    return out.astype(np.uint8)


# ---------------------------------------------------------------- 主流程

def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    img = trim_and_scale(cutout(Image.open(SRC)))
    arr = np.array(img)
    rgb, alpha = arr[:, :, :3], arr[:, :, 3]
    h, w = alpha.shape
    print(f"cutout {w}x{h}")

    head_rgba, body_a, head_pivot = split_head(rgb, alpha)
    print(f"head: cut y={head_pivot[1]:.0f} ({head_pivot[1] / h:.1%}), pivot x={head_pivot[0]:.0f}")

    layers: dict[str, np.ndarray] = {"head": head_rgba}
    pivots: dict[str, tuple[float, float]] = {"head": head_pivot}
    band_top, band_bot = int(h * 0.40), int(h * 0.75)
    for side, key in (("left", "armL"), ("right", "armR")):
        gaps = detect_arm_gaps(alpha, side, band_top, band_bot)
        result = split_arm(rgb, alpha, body_a, side, gaps) if gaps else None
        if result is None:
            print(f"{key}: 缝隙不足，放弃拆臂（该臂留在身体上）")
            continue
        arm_rgba, body_a, pivot = result
        layers[key] = arm_rgba
        pivots[key] = pivot
        print(f"{key}: 缝隙行 {len(gaps)}，pivot=({pivot[0]:.0f},{pivot[1]:.0f})")

    # 拆腿：胯线高度取躯干实体段中点作为胯中线
    hip_y = int(h * LEG_HIP_Y_FRAC)
    hip_runs = row_runs(alpha[hip_y])
    if hip_runs:
        trunk = max(hip_runs, key=lambda r: r[1] - r[0])
        crotch_x = (trunk[0] + trunk[1]) // 2
        for key in ("legL", "legR"):
            leg_rgba, body_a, pivot = split_leg(rgb, alpha, body_a, key, crotch_x)
            layers[key] = leg_rgba
            pivots[key] = pivot
        print(f"legs: crotch x={crotch_x}, hip y={hip_y}")
    else:
        print("legs: 胯部行无实体段，放弃拆腿")

    body_rgba = np.dstack([inpaint_body(rgb, body_a, alpha), body_a])

    Image.fromarray(body_rgba, "RGBA").save(OUT_DIR / "niulai-body.png")
    for key, layer in layers.items():
        Image.fromarray(layer, "RGBA").save(OUT_DIR / f"niulai-{key}.png")
    img.save(OUT_DIR / "niulai.png")  # 整图保留（兜底 / 静态展示）

    order = [
        body_rgba,
        layers.get("legL"),
        layers.get("legR"),
        layers.get("armL"),
        layers.get("armR"),
        layers["head"],
    ]
    stack = [layer for layer in order if layer is not None]
    check_dir = ROOT / "tmp_puppet_check"
    check_dir.mkdir(exist_ok=True)
    Image.fromarray(composite(stack), "RGBA").save(check_dir / "_puppet_check.png")

    # 抽帧校验：各层绕枢轴旋转后拼合，肉眼检查接缝/枢轴（角度取 CSS 动画的极值）
    frames = [composite(stack)]
    for key, degs in (
        ("head", (-10, 10)),
        ("armL", (-28, 28)),
        ("armR", (-28, 28)),
        ("legL", (-12, 12)),
        ("legR", (-12, 12)),
    ):
        if key not in layers:
            continue
        for deg in degs:
            posed = [
                rotate_layer(layers[key], pivots[key], deg) if layer is layers[key] else layer
                for layer in stack
            ]
            frames.append(composite(posed))
    strip = np.hstack([
        np.pad(f, ((4, 4), (4, 4), (0, 0)), constant_values=40) for f in frames
    ])
    Image.fromarray(strip, "RGBA").save(check_dir / "_puppet_motion.png")

    def pct(p: tuple[float, float]) -> tuple[float, float]:
        return (round(p[0] / w * 100, 1), round(p[1] / h * 100, 1))

    meta_lines = [
        "// 由 scripts/build_pet_puppet.py 生成，勿手改（重新生成素材时会被覆盖）",
        "/** 各图层 transform-origin 枢轴（画布宽高百分比，图层均为全画布尺寸） */",
        "export const NIULAI_PUPPET = {",
        f"  aspect: {round(w / h, 4)},",
    ]
    for key in ("head", "armL", "armR", "legL", "legR"):
        if key in pivots:
            x, y = pct(pivots[key])
            meta_lines.append(f"  {key}: {{ originX: {x}, originY: {y} }},")
        else:
            meta_lines.append(f"  {key}: null,")
    meta_lines.append("} as const")
    meta_lines.append("")
    (OUT_DIR / "niulai_puppet_meta.ts").write_text("\n".join(meta_lines), encoding="utf-8")
    print(f"OK -> {OUT_DIR} (layers: body, {', '.join(layers)})")


if __name__ == "__main__":
    main()
