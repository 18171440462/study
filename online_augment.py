"""
Online image augmentation utilities for OCR.

Features
  1) Anti-sharpen (反锐化): make edges less clear by blurring *edge regions only*.
  2) Gray gain (灰度增益): y = clip(x * a + b) to simulate strong/weak illumination.

Supports:
  - numpy images: HxWxC (uint8 RGB/BGR) or HxW (uint8)
  - torch tensors: CxHxW float (0..1) or uint8 (0..255)

Notes:
  - Both ops are designed to be "online" (on-the-fly) and fast.
  - Anti-sharpen defaults to non-geometric change, keeping boxes mostly valid.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Tuple, Union

import numpy as np


try:
    import cv2  # type: ignore
except Exception as e:  # pragma: no cover
    cv2 = None  # type: ignore
    _cv2_import_error = e


try:
    import torch
except Exception:  # pragma: no cover
    torch = None  # type: ignore


ArrayLike = Union[np.ndarray, "torch.Tensor"]


def _require_cv2() -> None:
    if cv2 is None:  # pragma: no cover
        raise RuntimeError(
            "opencv-python is required for anti_sharpen(). "
            f"Import error: {_cv2_import_error}"
        )


def _is_torch(x: ArrayLike) -> bool:
    return torch is not None and isinstance(x, torch.Tensor)


def _to_float01_numpy(img: np.ndarray) -> Tuple[np.ndarray, str]:
    """Return float image in [0,1] and a tag to restore."""
    if img.dtype == np.uint8:
        return img.astype(np.float32) / 255.0, "u8"
    if img.dtype in (np.float16, np.float32, np.float64):
        # assume already in [0,1] or [0,255]; we do not auto-scale to avoid surprises
        return img.astype(np.float32), "f"
    raise TypeError(f"Unsupported numpy dtype: {img.dtype}")


def _from_float01_numpy(img01: np.ndarray, tag: str) -> np.ndarray:
    if tag == "u8":
        return np.clip(np.rint(img01 * 255.0), 0, 255).astype(np.uint8)
    return img01.astype(np.float32)


def _to_float01_torch(x: "torch.Tensor") -> Tuple["torch.Tensor", str]:
    if x.dtype == torch.uint8:
        return x.float() / 255.0, "u8"
    return x.float(), "f"


def _from_float01_torch(x01: "torch.Tensor", tag: str, dtype: "torch.dtype") -> "torch.Tensor":
    if tag == "u8":
        return (x01.clamp(0, 1) * 255.0).round().to(torch.uint8)
    return x01.to(dtype)


def gray_gain(
    img: ArrayLike,
    a: float,
    b: float,
    clamp: Tuple[float, float] = (0.0, 1.0),
) -> ArrayLike:
    """
    Gray gain (brightness/contrast) via y = x*a + b.

    - For RGB: applies the same transform to each channel (simulates illumination).
    - For grayscale: applies directly.
    - Default clamp assumes float images in [0,1]. For uint8 inputs, we
      internally scale to [0,1] and return uint8.
    """
    if _is_torch(img):
        assert torch is not None
        x: "torch.Tensor" = img
        x01, tag = _to_float01_torch(x)
        y = x01 * float(a) + float(b)
        y = y.clamp(float(clamp[0]), float(clamp[1]))
        return _from_float01_torch(y, tag=tag, dtype=x.dtype)

    x_np = np.asarray(img)
    x01, tag = _to_float01_numpy(x_np)
    y = x01 * float(a) + float(b)
    y = np.clip(y, float(clamp[0]), float(clamp[1]))
    return _from_float01_numpy(y, tag=tag)


def anti_sharpen(
    img: ArrayLike,
    strength: float = 0.6,
    blur_sigma: float = 1.2,
    edge_threshold: float = 0.12,
    edge_softness: float = 0.08,
) -> ArrayLike:
    """
    Anti-sharpen (反锐化): blur edge areas so edges look less crisp.

    Implementation:
      - Compute an edge magnitude map (Sobel on luma).
      - Build a soft edge mask in [0,1] using threshold + softness.
      - Blur the whole image (Gaussian).
      - Blend: out = img*(1 - m*strength) + blur*(m*strength)

    Parameters:
      - strength: how much to blur edges (0..1 recommended).
      - blur_sigma: Gaussian blur sigma.
      - edge_threshold: edges below this (normalized) are mostly untouched.
      - edge_softness: transition width around threshold.
    """
    _require_cv2()

    def _anti_np(x_np: np.ndarray) -> np.ndarray:
        if x_np.ndim not in (2, 3):
            raise ValueError(f"Expected HxW or HxWxC, got shape={x_np.shape}")

        x01, tag = _to_float01_numpy(x_np)
        x01 = np.clip(x01, 0.0, 1.0)

        if x01.ndim == 2:
            luma = x01
            x3 = x01[:, :, None]
        else:
            # luma from RGB/BGR doesn't matter much for edge magnitude
            luma = (0.299 * x01[:, :, 0] + 0.587 * x01[:, :, 1] + 0.114 * x01[:, :, 2]).astype(np.float32)
            x3 = x01

        gx = cv2.Sobel(luma, cv2.CV_32F, 1, 0, ksize=3)
        gy = cv2.Sobel(luma, cv2.CV_32F, 0, 1, ksize=3)
        mag = cv2.magnitude(gx, gy)
        mag = mag / (float(mag.max()) + 1e-6)  # normalize 0..1

        t = float(edge_threshold)
        s = max(1e-6, float(edge_softness))
        m = (mag - t) / s
        m = np.clip(m, 0.0, 1.0)
        # Slight blur on mask to avoid harsh boundaries
        m = cv2.GaussianBlur(m, (0, 0), sigmaX=max(0.5, blur_sigma * 0.6))

        # blur full image
        blur = cv2.GaussianBlur(x3, (0, 0), sigmaX=float(blur_sigma), sigmaY=float(blur_sigma))
        w = np.clip(m[:, :, None] * float(strength), 0.0, 1.0)
        out = x3 * (1.0 - w) + blur * w

        if x01.ndim == 2:
            out = out[:, :, 0]
        return _from_float01_numpy(np.clip(out, 0.0, 1.0), tag=tag)

    if _is_torch(img):
        assert torch is not None
        x: "torch.Tensor" = img
        # Convert to numpy HWC float for cv2, then back.
        x01, tag = _to_float01_torch(x)
        if x01.dim() not in (2, 3):
            raise ValueError(f"Expected CHW or HW tensor, got shape={tuple(x01.shape)}")

        if x01.dim() == 3:
            chw = x01
            hwc = chw.permute(1, 2, 0).contiguous().cpu().numpy()
        else:
            hwc = x01.contiguous().cpu().numpy()

        out_np = _anti_np(hwc)
        out01_np, _ = _to_float01_numpy(np.asarray(out_np))
        out01 = torch.from_numpy(out01_np)
        if x01.dim() == 3:
            out01 = out01.permute(2, 0, 1).contiguous()
        return _from_float01_torch(out01.to(x.device), tag=tag, dtype=x.dtype)

    return _anti_np(np.asarray(img))


@dataclass(frozen=True)
class OnlineAugment:
    """
    Convenience wrapper to apply both ops with per-sample randomization.
    """

    anti_sharpen_p: float = 0.5
    gray_gain_p: float = 0.8

    # anti-sharpen params
    strength_range: Tuple[float, float] = (0.35, 0.85)
    blur_sigma_range: Tuple[float, float] = (0.8, 2.0)
    edge_threshold_range: Tuple[float, float] = (0.06, 0.18)
    edge_softness_range: Tuple[float, float] = (0.05, 0.12)

    # gray gain params: y=x*a+b (in [0,1])
    a_range: Tuple[float, float] = (0.65, 1.35)
    b_range: Tuple[float, float] = (-0.15, 0.15)

    seed: Optional[int] = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "_rng", np.random.default_rng(self.seed))

    def __call__(self, img: ArrayLike) -> ArrayLike:
        rng: np.random.Generator = getattr(self, "_rng")

        out = img
        if float(rng.random()) < float(self.gray_gain_p):
            a = float(rng.uniform(*self.a_range))
            b = float(rng.uniform(*self.b_range))
            out = gray_gain(out, a=a, b=b)

        if float(rng.random()) < float(self.anti_sharpen_p):
            strength = float(rng.uniform(*self.strength_range))
            blur_sigma = float(rng.uniform(*self.blur_sigma_range))
            edge_threshold = float(rng.uniform(*self.edge_threshold_range))
            edge_softness = float(rng.uniform(*self.edge_softness_range))
            out = anti_sharpen(
                out,
                strength=strength,
                blur_sigma=blur_sigma,
                edge_threshold=edge_threshold,
                edge_softness=edge_softness,
            )

        return out

