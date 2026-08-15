from pathlib import Path
from rich.console import Console
from transcripe.engines import ocr

_HEIF_REGISTERED = False


def _pil():
    """Lazy Pillow import (images extra) + one-time HEIC/AVIF plugin registration."""
    global _HEIF_REGISTERED
    try:
        from PIL import Image
    except ImportError as e:
        raise RuntimeError(
            "Image operations need Pillow — pip install 'transcripe[images]'") from e
    if not _HEIF_REGISTERED:
        _HEIF_REGISTERED = True
        # Register each opener on its own: pillow-heif ≥ 1.0 dropped
        # register_avif_opener (Pillow reads AVIF natively now), and importing
        # both together used to take HEIC support down with it.
        try:
            from pillow_heif import register_heif_opener
            register_heif_opener()
        except ImportError:
            pass
        try:
            from pillow_heif import register_avif_opener
            register_avif_opener()
        except ImportError:
            pass
    return Image


def _open_image(input_path: Path):
    """Open any supported image; rasterizes SVG (Pillow can't read vectors).

    SVG is vector, so its native pixel box is often tiny (e.g. a 170×50 logo).
    Rasterizing at 1× would produce a low-resolution PNG. We upscale so the
    larger side is at least TRANSCRIPE_SVG_MIN px (default 1920 — "full HD"),
    capped at 8× to avoid runaway sizes. Override with TRANSCRIPE_SVG_MIN.
    """
    import os
    Image = _pil()
    if input_path.suffix.lower() == ".svg":
        try:
            import cairosvg
        except ImportError:
            raise RuntimeError(
                "SVG input needs 'cairosvg' — pip install cairosvg "
                "(requires the system cairo library)")
        import io
        # First pass at 1× just to read the intrinsic size.
        base = Image.open(io.BytesIO(cairosvg.svg2png(url=str(input_path))))
        target_min = int(os.environ.get("TRANSCRIPE_SVG_MIN", "1920"))
        longest = max(base.width, base.height)
        # SVG is vector — re-rendering larger is crisp, never blurry — so we can
        # upscale freely to the target (capped at 30× as a runaway guard).
        scale = max(1.0, min(30.0, target_min / longest)) if longest else 1.0
        if scale > 1.0:
            png_bytes = cairosvg.svg2png(
                url=str(input_path),
                output_width=round(base.width * scale),
                output_height=round(base.height * scale))
            return Image.open(io.BytesIO(png_bytes))
        return base
    try:
        img = Image.open(input_path)
    except Exception as e:
        ext = input_path.suffix.lower()
        if ext in (".heic", ".avif"):
            raise RuntimeError(
                f"Cannot open {ext} — install the HEIF plugin: pip install pillow-heif") from e
        raise

    # Phone cameras store the sensor's own orientation and a tag saying how to
    # turn it. Without this a portrait photo converts to a sideways one — and
    # browsers, which honour the tag, would disagree with us about the same file.
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img) or img
    except Exception:
        pass
    return img


def get_reader():
    """Backwards-compatible EasyOCR reader accessor (prefer engines.ocr.ocr_image)."""
    return ocr._get_easy(("en",))

LOSSLESS_SOURCES = {".png", ".bmp", ".gif", ".tif", ".tiff", ".webp"}


def _save_options(img, target_format: str, source: Path | None = None) -> dict:
    """Encoder settings that keep the picture.

    Pillow defaults to JPEG quality 75 and WebP 80 — fine for a thumbnail,
    visibly soft for someone converting a photo they care about. Quality is
    the point of this tool, so ask for near-transparent settings and keep the
    colour profile that came in. Override the target with TRANSCRIPE_IMAGE_Q.
    """
    import os

    quality = int(os.environ.get("TRANSCRIPE_IMAGE_Q", "95"))
    opts: dict = {}

    # A colour profile is part of the picture: drop it and the colours shift.
    icc = img.info.get("icc_profile")
    if icc:
        opts["icc_profile"] = icc

    fmt = target_format.lower()
    if fmt in ("jpg", "jpeg"):
        opts.update(quality=quality, optimize=True, progressive=True,
                    # 4:4:4 — no chroma subsampling, so fine coloured detail
                    # (text, UI screenshots) survives.
                    subsampling=0)
        exif = img.info.get("exif")
        if exif:
            opts["exif"] = exif
    elif fmt == "webp":
        # Re-encoding something lossless into lossy WebP throws away detail for
        # no reason; keep it lossless when it arrived that way.
        if source and source.suffix.lower() in LOSSLESS_SOURCES:
            opts.update(lossless=True, quality=100, method=6)
        else:
            opts.update(quality=quality, method=6)
    elif fmt == "avif":
        # AVIF beats WebP at the same quality; keep a lossless source lossless.
        if source and source.suffix.lower() in LOSSLESS_SOURCES:
            opts.update(quality=100, lossless=True)
        else:
            opts.update(quality=quality)
    elif fmt == "ico":
        # Icons are a set of sizes, not one image; give the usual ones.
        opts["sizes"] = [(16, 16), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)]
    elif fmt == "png":
        opts.update(optimize=True, compress_level=9)
    elif fmt in ("tif", "tiff"):
        opts.update(compression="tiff_lzw")
    return opts


def convert_image(input_path: Path, target_format: str, console: Console,
                  output_path: Path | None = None, langs: list[str] | None = None):
    if target_format == "txt":
        # OCR
        engine = ocr.available_engine(langs)
        lang_label = ", ".join(langs) if langs else "auto"
        with console.status(f"[bold cyan]Running OCR on {input_path.name} ({engine}, {lang_label})…[/bold cyan]"):
            text = ocr.ocr_image(input_path, langs)

            out_path = output_path or input_path.with_suffix(".txt")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "w", encoding="utf-8") as f:
                f.write(text)

            console.print(f"[bold green]✓ OCR completed! Saved to {out_path.name}[/bold green] [dim]({len(text)} chars)[/dim]")

    elif target_format in ["png", "jpg", "jpeg", "webp", "bmp", "tiff", "gif", "ico", "avif"]:
        # Format conversion
        with console.status(f"[bold cyan]Converting image to {target_format.upper()}...[/bold cyan]"):
            img = _open_image(input_path)
            # Handle alpha channel if saving to jpeg
            if target_format in ["jpg", "jpeg"] and img.mode in ("RGBA", "P"):
                img = img.convert("RGB")

            out_path = output_path or input_path.with_suffix(f".{target_format}")
            out_path.parent.mkdir(parents=True, exist_ok=True)
            img.save(out_path, **_save_options(img, target_format, input_path))
            console.print(f"[bold green]✓ Converted! Saved to {out_path.name}[/bold green]")
    else:
        raise ValueError(f"Cannot convert image to {target_format}")


def resize_image(input_path: Path, width: int | None, height: int | None, console: Console, output_path: Path | None = None):
    """Resize an image. If only one dimension is given, the other scales proportionally."""
    img = _open_image(input_path)
    original_w, original_h = img.size

    if width and height:
        new_size = (width, height)
    elif width:
        ratio = width / original_w
        new_size = (width, int(original_h * ratio))
    elif height:
        ratio = height / original_h
        new_size = (int(original_w * ratio), height)
    else:
        console.print("[red]Please specify a width or height.[/red]")
        return

    with console.status(f"[bold cyan]Resizing {input_path.name} ({original_w}x{original_h} → {new_size[0]}x{new_size[1]})…[/bold cyan]"):
        img = img.resize(new_size, _pil().LANCZOS)

        out_path = output_path or (input_path.parent / f"{input_path.stem}_resized{input_path.suffix}")
        out_path.parent.mkdir(parents=True, exist_ok=True)
        # Handle alpha channel for jpeg
        if out_path.suffix.lower() in (".jpg", ".jpeg") and img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        img.save(out_path)

    console.print(f"[bold green]✓ Resized! {original_w}x{original_h} → {new_size[0]}x{new_size[1]}[/bold green]")
    console.print(f"Saved to: [bold underline]{out_path.name}[/bold underline]")


def compress_image(input_path: Path, quality: int, console: Console, output_path: Path | None = None):
    """Compress an image by reducing quality (1-100). Lower = smaller file."""
    img = _open_image(input_path)
    original_size = input_path.stat().st_size

    out_path = output_path or (input_path.parent / f"{input_path.stem}_compressed{input_path.suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Handle alpha channel for jpeg
    if out_path.suffix.lower() in (".jpg", ".jpeg") and img.mode in ("RGBA", "P"):
        img = img.convert("RGB")

    with console.status(f"[bold cyan]Compressing {input_path.name} (quality={quality})…[/bold cyan]"):
        save_kwargs = {"optimize": True}
        ext = out_path.suffix.lower()

        if ext in (".jpg", ".jpeg"):
            save_kwargs["quality"] = quality
        elif ext == ".png":
            save_kwargs["compress_level"] = min(9, max(0, (100 - quality) // 10))
        elif ext == ".webp":
            save_kwargs["quality"] = quality

        img.save(out_path, **save_kwargs)

    new_size = out_path.stat().st_size
    reduction = (1 - new_size / original_size) * 100 if original_size > 0 else 0
    orig_kb = original_size / 1024
    new_kb = new_size / 1024
    console.print(f"[bold green]✓ Compressed! {orig_kb:.0f} KB → {new_kb:.0f} KB ({reduction:.0f}% smaller)[/bold green]")
    console.print(f"Saved to: [bold underline]{out_path.name}[/bold underline]")


def parse_size(text: str) -> int:
    """Parse a human file size like '9.77KB', '2 MB', '500k', '10000' → bytes."""
    s = str(text).strip().upper().replace(" ", "")
    mult = 1
    for suffix, m in (("KB", 1024), ("K", 1024), ("MB", 1024 ** 2),
                      ("M", 1024 ** 2), ("GB", 1024 ** 3), ("G", 1024 ** 3), ("B", 1)):
        if s.endswith(suffix):
            s = s[:-len(suffix)]
            mult = m
            break
    return int(round(float(s) * mult))


def _save_variant(img, out_path: Path, quality: int | None = None):
    """Save img to out_path with format-appropriate options; returns byte size."""
    ext = out_path.suffix.lower()
    kw = {}
    if ext in (".jpg", ".jpeg"):
        if img.mode in ("RGBA", "P"):
            img = img.convert("RGB")
        kw = {"quality": quality if quality is not None else 90, "optimize": True}
    elif ext == ".webp":
        kw = {"quality": quality if quality is not None else 90}
    elif ext == ".png":
        kw = {"optimize": True}
    img.save(out_path, **kw)
    return out_path.stat().st_size


def fit_size(input_path: Path, console: Console, output_path: Path | None = None,
             min_bytes: int | None = None, max_bytes: int | None = None):
    """Re-encode an image so its file size lands within [min_bytes, max_bytes].

    Solves platform upload rules like "min 9.77 KB" (Google) or "max 2 MB".
    - Too small → upscale (and, for PNG, add a tiny metadata pad) until ≥ min.
    - Too large → shrink dimensions / lower quality until ≤ max.
    Both bounds may be given at once.
    """
    Image = _pil()
    img = _open_image(input_path)
    out_path = output_path or (input_path.parent / f"{input_path.stem}_fitted{input_path.suffix}")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    if min_bytes is None and max_bytes is None:
        raise ValueError("Give --min and/or --max a target size.")
    if min_bytes and max_bytes and min_bytes > max_bytes:
        raise ValueError("min size is larger than max size")

    ext = out_path.suffix.lower()
    orig = input_path.stat().st_size
    size = _save_variant(img, out_path)

    with console.status(f"[bold cyan]Fitting {input_path.name} to size…[/bold cyan]"):
        # ── Too big → shrink. Lower quality first (lossy formats), then scale. ──
        if max_bytes and size > max_bytes:
            quality = 92
            while size > max_bytes and quality > 20 and ext in (".jpg", ".jpeg", ".webp"):
                quality -= 8
                size = _save_variant(img, out_path, quality)
            while size > max_bytes and min(img.size) > 32:
                img = img.resize((max(1, int(img.width * 0.85)),
                                  max(1, int(img.height * 0.85))), Image.LANCZOS)
                size = _save_variant(img, out_path,
                                     quality if ext in (".jpg", ".jpeg", ".webp") else None)

        # ── Too small → upscale until we clear the floor. ──
        if min_bytes and size < min_bytes:
            for _ in range(12):
                if size >= min_bytes:
                    break
                img = img.resize((max(1, int(img.width * 1.4)),
                                  max(1, int(img.height * 1.4))), Image.LANCZOS)
                size = _save_variant(img, out_path)
            # Last resort for lossless PNG that is still under the floor: pad
            # trailing bytes in a private chunk so the file meets the minimum
            # without altering a single pixel.
            if min_bytes and size < min_bytes and ext == ".png":
                pad = min_bytes - size
                with open(out_path, "ab") as f:
                    f.write(b"\x00" * pad)  # after IEND; ignored by decoders
                size = out_path.stat().st_size

    within = (not min_bytes or size >= min_bytes) and (not max_bytes or size <= max_bytes)
    tag = "[bold green]✓" if within else "[bold yellow]⚠ (best effort)"
    bounds = []
    if min_bytes:
        bounds.append(f"min {min_bytes / 1024:.2f} KB")
    if max_bytes:
        bounds.append(f"max {max_bytes / 1024:.2f} KB")
    console.print(f"{tag} {orig / 1024:.1f} KB → {size / 1024:.1f} KB "
                  f"({img.width}x{img.height}, target {' & '.join(bounds)})[/]")
    console.print(f"Saved to: [bold underline]{out_path.name}[/bold underline]")
    if not within:
        raise RuntimeError(
            f"Could not fully reach the target ({size / 1024:.2f} KB). "
            "Try a different format (PNG grows more than JPEG).")
    return out_path


def image_to_pdf(input_path: Path, console: Console, output_path: Path | None = None):
    """Convert a single image to a PDF document."""
    img = _open_image(input_path).convert("RGB")
    out_path = output_path or input_path.with_suffix(".pdf")
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with console.status(f"[bold cyan]Converting {input_path.name} to PDF…[/bold cyan]"):
        img.save(out_path)

    console.print(f"[bold green]✓ Created {out_path.name}[/bold green]")
