#!/usr/bin/env python3
"""
Synthetic industrial OCR interference generator.

Goal:
  Take "clean" text images and procedurally add common industrial disturbances:
  - specular glare / reflection
  - ink stains / smears
  - overprint / ghosting / double-print
  - character sticking / adhesion (stroke thickening / bridging)
  - blur (Gaussian / motion-like) + JPEG artifacts
  - uneven illumination + vignetting
  - optional texture overlays from your "interference example" images

This script intentionally defaults to *non-geometric* perturbations so that
existing character bounding boxes remain usable in many detection pipelines.
"""

from __future__ import annotations

import argparse
import io
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, List, Optional, Tuple

import cv2  # type: ignore
import numpy as np
from PIL import Image, ImageDraw, ImageFont


IMG_EXTS = {".png", ".jpg", ".jpeg", ".bmp", ".tif", ".tiff", ".webp"}


def _imread_rgb(path: Path) -> np.ndarray:
    img = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
    if img is None:
        raise FileNotFoundError(f"Failed to read image: {path}")

    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)

    if img.shape[2] == 4:
        # Alpha composite over white background
        bgr = img[:, :, :3].astype(np.float32)
        a = (img[:, :, 3:4].astype(np.float32) / 255.0)
        white = np.full_like(bgr, 255.0)
        bgr = bgr * a + white * (1.0 - a)
        img = bgr.astype(np.uint8)

    rgb = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2RGB)
    return rgb


def _imwrite(path: Path, rgb: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
    ok = cv2.imwrite(str(path), bgr)
    if not ok:
        raise RuntimeError(f"Failed to write image: {path}")


def _to_float01(rgb: np.ndarray) -> np.ndarray:
    return np.clip(rgb.astype(np.float32) / 255.0, 0.0, 1.0)


def _to_uint8(rgb01: np.ndarray) -> np.ndarray:
    return np.clip(np.rint(rgb01 * 255.0), 0, 255).astype(np.uint8)


def _rgb_to_gray01(rgb01: np.ndarray) -> np.ndarray:
    # ITU-R BT.601 luma
    return (0.299 * rgb01[:, :, 0] + 0.587 * rgb01[:, :, 1] + 0.114 * rgb01[:, :, 2]).astype(np.float32)


def _screen(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    # screen blend in [0,1]
    return 1.0 - (1.0 - a) * (1.0 - b)


def _multiply(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    return a * b


def _rand_choice(rng: np.random.Generator, items: List[Path]) -> Path:
    return items[int(rng.integers(0, len(items)))]


def _estimate_text_mask(rgb01: np.ndarray) -> np.ndarray:
    """
    Try to segment text vs background using Otsu on grayscale.
    Returns mask of likely text pixels (True=text).
    """
    gray = _rgb_to_gray01(rgb01)
    g8 = np.clip(gray * 255.0, 0, 255).astype(np.uint8)
    _, th = cv2.threshold(g8, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # th==255 means "foreground" depending on polarity. Decide by which side is darker.
    fg = th == 0
    bg = th == 255
    if fg.sum() == 0 or bg.sum() == 0:
        # fallback: assume darker is text
        return gray < np.median(gray)
    if gray[fg].mean() < gray[bg].mean():
        return fg
    return bg


@dataclass(frozen=True)
class Preset:
    glare_p: float
    ink_p: float
    overprint_p: float
    stick_p: float
    blur_p: float
    jpeg_p: float
    illum_p: float
    texture_p: float

    glare_alpha: Tuple[float, float]
    ink_alpha: Tuple[float, float]
    overprint_alpha: Tuple[float, float]
    stick_strength: Tuple[float, float]
    blur_sigma: Tuple[float, float]
    jpeg_quality: Tuple[int, int]
    illum_strength: Tuple[float, float]
    texture_alpha: Tuple[float, float]


PRESETS = {
    "light": Preset(
        glare_p=0.35,
        ink_p=0.25,
        overprint_p=0.35,
        stick_p=0.20,
        blur_p=0.35,
        jpeg_p=0.35,
        illum_p=0.50,
        texture_p=0.30,
        glare_alpha=(0.10, 0.30),
        ink_alpha=(0.10, 0.35),
        overprint_alpha=(0.10, 0.30),
        stick_strength=(0.10, 0.35),
        blur_sigma=(0.3, 1.0),
        jpeg_quality=(55, 90),
        illum_strength=(0.10, 0.35),
        texture_alpha=(0.05, 0.20),
    ),
    "medium": Preset(
        glare_p=0.50,
        ink_p=0.40,
        overprint_p=0.50,
        stick_p=0.35,
        blur_p=0.50,
        jpeg_p=0.50,
        illum_p=0.65,
        texture_p=0.45,
        glare_alpha=(0.15, 0.50),
        ink_alpha=(0.15, 0.55),
        overprint_alpha=(0.15, 0.45),
        stick_strength=(0.20, 0.55),
        blur_sigma=(0.6, 1.8),
        jpeg_quality=(35, 80),
        illum_strength=(0.20, 0.55),
        texture_alpha=(0.08, 0.30),
    ),
    "heavy": Preset(
        glare_p=0.65,
        ink_p=0.55,
        overprint_p=0.65,
        stick_p=0.55,
        blur_p=0.65,
        jpeg_p=0.65,
        illum_p=0.80,
        texture_p=0.60,
        glare_alpha=(0.25, 0.75),
        ink_alpha=(0.25, 0.75),
        overprint_alpha=(0.25, 0.65),
        stick_strength=(0.35, 0.85),
        blur_sigma=(1.0, 3.0),
        jpeg_quality=(20, 65),
        illum_strength=(0.35, 0.85),
        texture_alpha=(0.12, 0.45),
    ),
}


def _apply_glare(rgb01: np.ndarray, rng: np.random.Generator, alpha: float) -> np.ndarray:
    h, w = rgb01.shape[:2]
    mask = np.zeros((h, w), np.float32)

    # Random ellipse "flare" + optional streak
    cx = float(rng.uniform(0.1, 0.9) * w)
    cy = float(rng.uniform(0.1, 0.9) * h)
    ax = float(rng.uniform(0.20, 0.70) * w)
    ay = float(rng.uniform(0.08, 0.35) * h)
    angle = float(rng.uniform(0, 180))

    cv2.ellipse(mask, (int(cx), int(cy)), (int(ax / 2), int(ay / 2)), angle, 0, 360, 1.0, -1)
    blur = float(rng.uniform(15, 65))
    k = int(blur) * 2 + 1
    mask = cv2.GaussianBlur(mask, (k, k), blur)

    if rng.random() < 0.5:
        # Add a faint streak
        x1, y1 = int(rng.uniform(0, w)), int(rng.uniform(0, h))
        x2, y2 = int(rng.uniform(0, w)), int(rng.uniform(0, h))
        cv2.line(mask, (x1, y1), (x2, y2), 1.0, int(rng.integers(2, 8)))
        mask = cv2.GaussianBlur(mask, (k, k), blur)

    mask = np.clip(mask / (mask.max() + 1e-6), 0.0, 1.0)
    glare = np.repeat(mask[:, :, None], 3, axis=2)
    # screen with white glare
    out = _screen(rgb01, glare * alpha)
    return np.clip(out, 0.0, 1.0)


def _apply_uneven_illumination(rgb01: np.ndarray, rng: np.random.Generator, strength: float) -> np.ndarray:
    h, w = rgb01.shape[:2]
    yy, xx = np.mgrid[0:h, 0:w].astype(np.float32)
    xx = (xx / max(w - 1, 1)) * 2 - 1
    yy = (yy / max(h - 1, 1)) * 2 - 1

    # Random gradient direction + vignetting
    dx = float(rng.uniform(-1, 1))
    dy = float(rng.uniform(-1, 1))
    grad = dx * xx + dy * yy
    grad = (grad - grad.min()) / (grad.max() - grad.min() + 1e-6)

    r2 = xx * xx + yy * yy
    vign = np.clip(1.0 - r2 * float(rng.uniform(0.2, 0.9)), 0.3, 1.0)

    # illumination map in [1-strength, 1+strength], then multiply
    illum = (1.0 - strength) + (2.0 * strength) * grad
    illum = illum * vign
    illum = np.clip(illum, 0.2, 1.8)

    out = rgb01 * illum[:, :, None]
    return np.clip(out, 0.0, 1.0)


def _apply_ink_stain(rgb01: np.ndarray, rng: np.random.Generator, alpha: float) -> np.ndarray:
    h, w = rgb01.shape[:2]
    noise = rng.random((h, w), dtype=np.float32)
    # Make blobby regions by blurring noise heavily then thresholding
    sigma = float(rng.uniform(10, 45))
    k = int(sigma) * 2 + 1
    field = cv2.GaussianBlur(noise, (k, k), sigma)
    field = (field - field.min()) / (field.max() - field.min() + 1e-6)

    t = float(rng.uniform(0.70, 0.88))
    blob = np.clip((field - t) / (1.0 - t + 1e-6), 0.0, 1.0)

    # Add a few darker drops
    drops = np.zeros((h, w), np.float32)
    for _ in range(int(rng.integers(2, 10))):
        cx = int(rng.integers(0, w))
        cy = int(rng.integers(0, h))
        rr = int(rng.integers(max(3, min(h, w) // 60), max(6, min(h, w) // 12)))
        cv2.circle(drops, (cx, cy), rr, float(rng.uniform(0.4, 1.0)), -1)
    drops = cv2.GaussianBlur(drops, (0, 0), float(rng.uniform(2, 8)))

    mask = np.clip(blob + drops, 0.0, 1.0)
    mask = np.power(mask, float(rng.uniform(1.2, 2.2)))

    # Darken: multiply by (1 - alpha*mask)
    dark = 1.0 - alpha * mask
    out = rgb01 * dark[:, :, None]
    return np.clip(out, 0.0, 1.0)


def _apply_overprint(rgb01: np.ndarray, rng: np.random.Generator, alpha: float) -> np.ndarray:
    h, w = rgb01.shape[:2]
    dx = int(rng.integers(-max(1, w // 200), max(2, w // 120)))
    dy = int(rng.integers(-max(1, h // 200), max(2, h // 120)))
    m = np.float32([[1, 0, dx], [0, 1, dy]])
    shifted = cv2.warpAffine(rgb01, m, (w, h), flags=cv2.INTER_LINEAR, borderMode=cv2.BORDER_REPLICATE)

    # Overprint is usually darker (double ink): take min between original and blended shifted
    blended = rgb01 * (1.0 - alpha) + shifted * alpha
    out = np.minimum(rgb01, blended)
    return np.clip(out, 0.0, 1.0)


def _apply_stickiness(rgb01: np.ndarray, rng: np.random.Generator, strength: float) -> np.ndarray:
    """
    Simulate character adhesion/bridging by dilating estimated text mask
    and darkening newly covered pixels.
    """
    gray = _rgb_to_gray01(rgb01)
    text = _estimate_text_mask(rgb01)
    text_u8 = (text.astype(np.uint8) * 255)

    k = int(rng.integers(1, 4)) * 2 + 1
    kernel = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k))
    dil = cv2.dilate(text_u8, kernel, iterations=1) > 0

    added = np.logical_and(dil, ~text)
    if added.sum() == 0:
        return rgb01

    # Determine "ink level" from existing text pixels (darker percentile)
    if text.sum() > 10:
        ink_level = float(np.percentile(gray[text], 20))
    else:
        ink_level = 0.15

    # Darken added pixels towards ink_level
    out = rgb01.copy()
    target = np.clip(ink_level + float(rng.uniform(0.0, 0.12)), 0.0, 1.0)
    blend = np.clip(float(rng.uniform(strength * 0.7, strength)), 0.0, 1.0)
    for c in range(3):
        out[:, :, c][added] = np.minimum(out[:, :, c][added], out[:, :, c][added] * (1.0 - blend) + target * blend)
    return np.clip(out, 0.0, 1.0)


def _apply_blur(rgb01: np.ndarray, rng: np.random.Generator, sigma: float) -> np.ndarray:
    # Sometimes motion-ish blur, sometimes Gaussian
    if rng.random() < 0.35:
        k = int(max(3, int(sigma * 6))) | 1
        kernel = np.zeros((k, k), np.float32)
        kernel[k // 2, :] = 1.0
        kernel /= kernel.sum()
        angle = float(rng.uniform(0, 180))
        # rotate kernel
        rot = cv2.getRotationMatrix2D((k / 2 - 0.5, k / 2 - 0.5), angle, 1.0)
        kernel = cv2.warpAffine(kernel, rot, (k, k))
        kernel = np.clip(kernel, 0, None)
        kernel /= (kernel.sum() + 1e-6)
        out = cv2.filter2D(rgb01, -1, kernel)
        return np.clip(out, 0.0, 1.0)

    k = int(max(3, int(sigma * 6))) | 1
    out = cv2.GaussianBlur(rgb01, (k, k), sigmaX=sigma, sigmaY=sigma)
    return np.clip(out, 0.0, 1.0)


def _apply_jpeg(rgb01: np.ndarray, rng: np.random.Generator, quality: int) -> np.ndarray:
    rgb8 = _to_uint8(rgb01)
    pil = Image.fromarray(rgb8, mode="RGB")
    buf = io.BytesIO()
    pil.save(buf, format="JPEG", quality=int(quality), subsampling=2, optimize=False)
    buf.seek(0)
    out = np.asarray(Image.open(buf).convert("RGB"))
    return _to_float01(out)


def _apply_texture_overlay(rgb01: np.ndarray, tex_rgb01: np.ndarray, rng: np.random.Generator, alpha: float) -> np.ndarray:
    h, w = rgb01.shape[:2]
    th, tw = tex_rgb01.shape[:2]

    # random crop from texture then resize to image
    if th < 8 or tw < 8:
        return rgb01
    ch = int(rng.integers(max(8, th // 4), th + 1))
    cw = int(rng.integers(max(8, tw // 4), tw + 1))
    y0 = int(rng.integers(0, max(1, th - ch + 1)))
    x0 = int(rng.integers(0, max(1, tw - cw + 1)))
    crop = tex_rgb01[y0 : y0 + ch, x0 : x0 + cw, :]
    crop = cv2.resize(crop, (w, h), interpolation=cv2.INTER_LINEAR)

    # convert to "texture" intensity map
    tgray = _rgb_to_gray01(crop)
    tgray = (tgray - tgray.min()) / (tgray.max() - tgray.min() + 1e-6)
    tgray = np.power(tgray, float(rng.uniform(0.6, 2.0)))
    tex = np.repeat(tgray[:, :, None], 3, axis=2)

    if rng.random() < 0.5:
        # multiply dark speckles
        out = _multiply(rgb01, 1.0 - alpha * (1.0 - tex))
    else:
        # screen bright haze
        out = _screen(rgb01, tex * alpha)
    return np.clip(out, 0.0, 1.0)


def augment_one(
    rgb: np.ndarray,
    rng: np.random.Generator,
    preset: Preset,
    texture_paths: Optional[List[Path]] = None,
) -> np.ndarray:
    rgb01 = _to_float01(rgb)

    if rng.random() < preset.illum_p:
        strength = float(rng.uniform(*preset.illum_strength))
        rgb01 = _apply_uneven_illumination(rgb01, rng, strength=strength)

    if texture_paths and rng.random() < preset.texture_p:
        tpath = _rand_choice(rng, texture_paths)
        tex = _to_float01(_imread_rgb(tpath))
        alpha = float(rng.uniform(*preset.texture_alpha))
        rgb01 = _apply_texture_overlay(rgb01, tex, rng, alpha=alpha)

    if rng.random() < preset.overprint_p:
        alpha = float(rng.uniform(*preset.overprint_alpha))
        rgb01 = _apply_overprint(rgb01, rng, alpha=alpha)

    if rng.random() < preset.stick_p:
        strength = float(rng.uniform(*preset.stick_strength))
        rgb01 = _apply_stickiness(rgb01, rng, strength=strength)

    if rng.random() < preset.ink_p:
        alpha = float(rng.uniform(*preset.ink_alpha))
        rgb01 = _apply_ink_stain(rgb01, rng, alpha=alpha)

    if rng.random() < preset.glare_p:
        alpha = float(rng.uniform(*preset.glare_alpha))
        rgb01 = _apply_glare(rgb01, rng, alpha=alpha)

    if rng.random() < preset.blur_p:
        sigma = float(rng.uniform(*preset.blur_sigma))
        rgb01 = _apply_blur(rgb01, rng, sigma=sigma)

    if rng.random() < preset.jpeg_p:
        q = int(rng.integers(preset.jpeg_quality[0], preset.jpeg_quality[1] + 1))
        rgb01 = _apply_jpeg(rgb01, rng, quality=q)

    return _to_uint8(rgb01)


def list_images(path: Path) -> List[Path]:
    if path.is_file():
        return [path]
    out: List[Path] = []
    for p in sorted(path.rglob("*")):
        if p.is_file() and p.suffix.lower() in IMG_EXTS:
            out.append(p)
    return out


def _load_textures(texture_dir: Optional[Path]) -> Optional[List[Path]]:
    if not texture_dir:
        return None
    if not texture_dir.exists():
        raise FileNotFoundError(f"Texture dir not found: {texture_dir}")
    tex = list_images(texture_dir)
    return tex or None


def _make_demo_images(out_dir: Path, n: int, rng: np.random.Generator) -> List[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    # Use PIL built-in default font for portability.
    font = ImageFont.load_default()

    samples: List[Path] = []
    alphabet = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    for i in range(n):
        w, h = int(rng.integers(240, 520)), int(rng.integers(64, 160))
        bg = Image.new("RGB", (w, h), (255, 255, 255))
        d = ImageDraw.Draw(bg)
        text = "".join(alphabet[int(rng.integers(0, len(alphabet)))] for _ in range(int(rng.integers(6, 16))))
        x = int(rng.integers(8, 20))
        y = int(rng.integers(8, 30))
        # Random dark ink-ish color
        ink = int(rng.integers(0, 60))
        d.text((x, y), text, font=font, fill=(ink, ink, ink))
        p = out_dir / f"demo_clean_{i:03d}.png"
        bg.save(p)
        samples.append(p)
    return samples


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Generate industrial OCR interference from clean text images.")
    ap.add_argument("--input", type=str, default=None, help="Input image file or directory of clean images.")
    ap.add_argument("--output", type=str, required=True, help="Output directory.")
    ap.add_argument("--textures", type=str, default=None, help="Directory of interference example images (used as overlay textures).")
    ap.add_argument("--preset", type=str, default="medium", choices=sorted(PRESETS.keys()), help="Augmentation strength preset.")
    ap.add_argument("--num", type=int, default=1, help="How many outputs per input image.")
    ap.add_argument("--seed", type=int, default=0, help="Random seed (0 means random).")
    ap.add_argument("--keep_structure", action="store_true", help="Preserve relative input directory structure in output.")
    ap.add_argument("--demo", type=int, default=0, help="If >0, generate N demo clean images first then augment them.")
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    out_dir = Path(args.output)
    out_dir.mkdir(parents=True, exist_ok=True)

    seed = int(args.seed)
    if seed == 0:
        seed = int.from_bytes(os.urandom(8), "little", signed=False) & 0x7FFFFFFF
    rng = np.random.default_rng(seed)

    preset = PRESETS[str(args.preset)]
    textures = _load_textures(Path(args.textures)) if args.textures else None

    if int(args.demo) > 0:
        demo_dir = out_dir / "_demo_clean"
        inputs = _make_demo_images(demo_dir, int(args.demo), rng=rng)
        input_root = demo_dir
    else:
        if not args.input:
            raise SystemExit("--input is required unless --demo > 0")
        input_path = Path(args.input)
        inputs = list_images(input_path)
        if not inputs:
            raise SystemExit(f"No images found under: {input_path}")
        input_root = input_path if input_path.is_dir() else input_path.parent

    for ip in inputs:
        rgb = _imread_rgb(ip)
        for k in range(int(args.num)):
            aug = augment_one(rgb, rng=rng, preset=preset, texture_paths=textures)
            stem = ip.stem
            rel = ip.relative_to(input_root) if args.keep_structure and ip.is_relative_to(input_root) else Path(ip.name)
            out_subdir = out_dir / rel.parent
            out_name = f"{stem}__aug{k:02d}.png"
            _imwrite(out_subdir / out_name, aug)

    print(f"Done. seed={seed} preset={args.preset} textures={len(textures) if textures else 0}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

