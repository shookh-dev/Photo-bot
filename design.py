"""Reklama rasmlarini chizish (faqat Pillow + numpy)."""
import colorsys
import io
import math
import os

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

HERE = os.path.dirname(os.path.abspath(__file__))
SANS_B, SANS, SERIF_B = "DejaVuSans-Bold.ttf", "DejaVuSans.ttf", "Lora-Bold.ttf"

FORMATS = {"portrait": (1080, 1350), "post": (1080, 1080), "story": (1080, 1920)}
FORMAT_NAMES = {"portrait": "📐 4:5", "post": "⬜ Post", "story": "📱 Story"}
STYLES = ["color", "luxury", "minimal", "sale"]
STYLE_NAMES = {"color": "🎨 Rangli", "luxury": "🌙 Premium", "minimal": "✨ Minimal", "sale": "🔥 Chegirma"}

_cache = {}


def font(name: str, size: int):
    key = (name, size)
    if key in _cache:
        return _cache[key]
    candidates = [os.path.join(HERE, name), name, os.path.join(HERE, SANS_B), SANS_B]
    try:
        import matplotlib
        candidates.append(os.path.join(matplotlib.get_data_path(), "fonts", "ttf", "DejaVuSans-Bold.ttf"))
    except Exception:
        pass
    f = None
    for c in candidates:
        try:
            f = ImageFont.truetype(c, size)
            break
        except OSError:
            continue
    if f is None:
        try:
            f = ImageFont.load_default(size=size)
        except TypeError:
            f = ImageFont.load_default()
    _cache[key] = f
    return f


def hsv(h, s, v):
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0, min(1, s)), max(0, min(1, v)))
    return (int(r * 255), int(g * 255), int(b * 255))


# ---------- Asosiy yordamchilar ----------
def prep(img: Image.Image) -> Image.Image:
    """Mahsulotni tiniqlashtiradi: kontrast, rang, o'tkirlik."""
    img = img.convert("RGBA")
    a = img.getchannel("A")
    rgb = img.convert("RGB")
    rgb = ImageEnhance.Contrast(rgb).enhance(1.08)
    rgb = ImageEnhance.Color(rgb).enhance(1.12)
    rgb = ImageEnhance.Sharpness(rgb).enhance(1.5)
    out = rgb.convert("RGBA")
    out.putalpha(a)
    return out


def dominant(img: Image.Image):
    """Mahsulotning asosiy rangi: (hue, saturation)."""
    small = img.resize((48, 48))
    px = [p for p in small.getdata() if p[3] > 200]
    if not px:
        return 0.6, 0.5
    r = sum(p[0] for p in px) / len(px)
    g = sum(p[1] for p in px) / len(px)
    b = sum(p[2] for p in px) / len(px)
    h, s, _ = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
    if s < 0.18:  # oq/kulrang/qora mahsulot
        h, s = 0.6, 0.5
    return h, s


def vgrad(w, h, top, bot):
    t = np.linspace(0, 1, h)[:, None, None]
    arr = np.array(top, dtype=float)[None, None, :] * (1 - t) + np.array(bot, dtype=float)[None, None, :] * t
    arr = np.repeat(arr, w, axis=1).astype("uint8")
    return Image.fromarray(arr, "RGB").convert("RGBA")


def glow(canvas, cx, cy, r, color, strength=0.5):
    w, h = canvas.size
    y, x = np.ogrid[:h, :w]
    d = np.sqrt((x - cx) ** 2 + (y - cy) ** 2) / r
    a = np.clip(1 - d, 0, 1) ** 1.6 * strength
    arr = np.zeros((h, w, 4), dtype="uint8")
    arr[..., 0], arr[..., 1], arr[..., 2] = color
    arr[..., 3] = (a * 255).astype("uint8")
    return Image.alpha_composite(canvas, Image.fromarray(arr, "RGBA"))


def overlay_shape(canvas, draw_fn, blur=0):
    layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    draw_fn(ImageDraw.Draw(layer))
    if blur:
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
    return Image.alpha_composite(canvas, layer)


def place(canvas, prod, area, shadow=0.35, scale=0.92, reflect=False):
    """Mahsulotni area ichiga joylaydi (soya/aks bilan). area=(x0,y0,x1,y1)."""
    x0, y0, x1, y1 = area
    bw, bh = (x1 - x0) * scale, (y1 - y0) * scale * (0.78 if reflect else 1.0)
    ratio = min(bw / prod.width, bh / prod.height, 3.0)
    p = prod.resize((max(1, int(prod.width * ratio)), max(1, int(prod.height * ratio))), Image.LANCZOS)

    total_h = p.height * (1.28 if reflect else 1.0)
    px = int((x0 + x1) / 2 - p.width / 2)
    py = int(y0 + ((y1 - y0) - total_h) / 2)
    base = py + p.height

    if shadow:
        mask = p.getchannel("A").point(lambda v: int(v * shadow))
        layer = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
        layer.paste((0, 0, 0, 255), (px + int(p.width * 0.02), py + int(p.height * 0.025)), mask)
        canvas = Image.alpha_composite(canvas, layer.filter(ImageFilter.GaussianBlur(max(4, p.width * 0.03))))
        ew = p.width * 0.8
        cx = px + p.width / 2
        canvas = overlay_shape(
            canvas,
            lambda d: d.ellipse((cx - ew / 2, base - p.width * 0.04, cx + ew / 2, base + p.width * 0.05),
                                fill=(0, 0, 0, int(255 * shadow * 0.9))),
            blur=max(5, p.width * 0.03),
        )

    if reflect:
        rh = int(p.height * 0.28)
        flip = ImageOps.flip(p).crop((0, 0, p.width, rh))
        fade = np.linspace(0.28, 0, rh)[:, None]
        fa = (np.array(flip.getchannel("A"), dtype=float) * fade).astype("uint8")
        flip.putalpha(Image.fromarray(fa))
        canvas.alpha_composite(flip, (px, base + 2))

    canvas.alpha_composite(p, (px, py))
    return canvas


def fit_font(d, text, name, max_w, start, min_size=26):
    size = start
    while size > min_size and d.textlength(text, font=font(name, size)) > max_w:
        size -= 2
    return font(name, size)


def wrap(d, text, f, max_w):
    lines, cur = [], ""
    for w in text.split():
        t = (cur + " " + w).strip()
        if d.textlength(t, font=f) <= max_w or not cur:
            cur = t
        else:
            lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return lines


def text_block(canvas, y0, y1, name, price, contact, *, title_font, title_col, upper,
               price_font, price_col, pill_col=None, pill_text=None, contact_col=(120, 120, 120)):
    """Nom, narx va aloqani [y0, y1] oralig'ida o'rtaga joylaydi."""
    W = canvas.width
    d = ImageDraw.Draw(canvas)
    max_w = int(W * 0.84)

    lines, tf = [], None
    if name:
        t = name.upper() if upper else name
        size = int(W * 0.068)
        while True:
            tf = font(title_font, size)
            lines = wrap(d, t, tf, max_w)
            ok = len(lines) <= 2 and all(d.textlength(l, font=tf) <= max_w for l in lines)
            if ok or size <= int(W * 0.04):
                break
            size -= 3
        lines = lines[:2]
    lh = int(tf.size * 1.2) if tf else 0
    title_h = lh * len(lines)

    pf = fit_font(d, price, price_font, int(W * 0.66), int(W * 0.082)) if price else None
    pad_y = int(pf.size * 0.3) if pf else 0
    price_h = int(pf.size * 1.2) + (2 * pad_y if pill_col else 0) if pf else 0

    cf = font(SANS, int(W * 0.037)) if contact else None
    contact_h = int(cf.size * 1.4) if cf else 0

    gap1 = int(W * 0.03) if (lines and pf) else 0
    gap2 = int(W * 0.025) if (contact and (lines or pf)) else 0
    total = title_h + gap1 + price_h + gap2 + contact_h
    if total > (y1 - y0) * 0.96 and tf is not None:
        k = (y1 - y0) * 0.96 / total
        tf = font(title_font, max(24, int(tf.size * k)))
        lh = int(tf.size * 1.2)
        title_h = lh * len(lines)
        if pf:
            pf = font(price_font, max(24, int(pf.size * k)))
            pad_y = int(pf.size * 0.3)
            price_h = int(pf.size * 1.2) + (2 * pad_y if pill_col else 0)
        total = title_h + gap1 + price_h + gap2 + contact_h
    y = y0 + (y1 - y0 - total) / 2

    for line in lines:
        d.text((W / 2, y), line, font=tf, fill=title_col, anchor="mt")
        y += lh
    y += gap1

    if pf:
        if pill_col:
            tw = d.textlength(price, font=pf)
            px_ = pf.size * 0.65
            d.rounded_rectangle((W / 2 - tw / 2 - px_, y, W / 2 + tw / 2 + px_, y + price_h),
                                radius=price_h / 2, fill=pill_col)
            d.text((W / 2, y + price_h / 2), price, font=pf, fill=pill_text, anchor="mm")
        else:
            d.text((W / 2, y), price, font=pf, fill=price_col, anchor="mt")
        y += price_h
    y += gap2
    if cf:
        d.text((W / 2, y), contact, font=cf, fill=contact_col, anchor="mt")
    return canvas


def footer(canvas, brand, color):
    if not brand:
        return
    W, H = canvas.size
    d = ImageDraw.Draw(canvas)
    f = fit_font(d, brand, SANS_B, int(W * 0.8), int(W * 0.034), 18)
    d.text((W / 2, H - int(W * 0.03)), brand, font=f, fill=color, anchor="mb")


def badge(canvas, text, cx, cy, r):
    S = 2
    sz = int(r * 2.3 * S)
    layer = Image.new("RGBA", (sz, sz), (0, 0, 0, 0))
    d = ImageDraw.Draw(layer)
    c, n = sz / 2, 18
    pts = []
    for i in range(n * 2):
        ang = math.pi * i / n
        rad = r * S if i % 2 == 0 else r * S * 0.86
        pts.append((c + rad * math.cos(ang), c + rad * math.sin(ang)))
    d.polygon(pts, fill=(255, 214, 10, 255))
    f = fit_font(d, text, SANS_B, r * S * 1.4, int(r * S * 0.5), 20)
    d.text((c, c), text, font=f, fill=(190, 20, 30, 255), anchor="mm")
    layer = layer.resize((sz // S, sz // S), Image.LANCZOS).rotate(-14, expand=True, resample=Image.BICUBIC)
    canvas.alpha_composite(layer, (int(cx - layer.width / 2), int(cy - layer.height / 2)))
    return canvas


def to_jpeg(canvas) -> bytes:
    out = io.BytesIO()
    canvas.convert("RGB").save(out, "JPEG", quality=95, subsampling=0)
    return out.getvalue()


# ---------- Uslublar ----------
def _zones(W, H, brand):
    tz = int(W * 0.47 + max(0, H - 1350) * 0.12)
    foot = int(W * 0.085) if brand else int(W * 0.035)
    return tz, foot


def style_minimal(W, H, prod, name, price, contact, brand, sale):
    tz, foot = _zones(W, H, brand)
    h, s = dominant(prod)
    canvas = vgrad(W, H, (250, 248, 245), (226, 222, 216))
    canvas = glow(canvas, W / 2, (H - tz) * 0.5, W * 0.62, (255, 255, 255), 0.9)
    canvas = place(canvas, prod, (0, int(H * 0.04), W, H - tz), shadow=0.32)
    text_block(canvas, H - tz, H - foot, name, price, contact,
               title_font=SANS_B, title_col=(28, 28, 32), upper=False,
               price_font=SANS_B, price_col=None, pill_col=hsv(h, 0.75, 0.5), pill_text=(255, 255, 255),
               contact_col=(110, 110, 116))
    footer(canvas, brand, (140, 140, 146))
    return canvas


def style_color(W, H, prod, name, price, contact, brand, sale):
    tz, foot = _zones(W, H, brand)
    h, s = dominant(prod)
    hc = (h + 0.5) % 1.0
    canvas = vgrad(W, H, hsv(hc, 0.14, 1.0), hsv(hc, 0.5, 0.96))
    canvas = overlay_shape(canvas, lambda d: d.ellipse((-W * 0.3, -W * 0.25, W * 0.75, W * 0.8), fill=(255, 255, 255, 70)))
    canvas = overlay_shape(canvas, lambda d: d.ellipse((W * 0.45, H * 0.25, W * 1.4, H * 0.25 + W * 0.95), fill=(255, 255, 255, 45)))
    card_top = H - tz - int(W * 0.01)
    canvas = place(canvas, prod, (0, int(H * 0.035), W, card_top), shadow=0.3)
    m = int(W * 0.05)
    box = (m, card_top, W - m, H - foot)
    canvas = overlay_shape(canvas, lambda d: d.rounded_rectangle((box[0], box[1] + 10, box[2], box[3] + 10), 56, fill=(0, 0, 0, 70)), blur=18)
    ImageDraw.Draw(canvas).rounded_rectangle(box, 56, fill=(255, 255, 255, 255))
    text_block(canvas, box[1] + int(W * 0.025), box[3] - int(W * 0.01), name, price, contact,
               title_font=SANS_B, title_col=(30, 30, 36), upper=False,
               price_font=SANS_B, price_col=None, pill_col=hsv(hc, 0.85, 0.45), pill_text=(255, 255, 255),
               contact_col=(120, 120, 128))
    footer(canvas, brand, hsv(hc, 0.7, 0.4))
    return canvas


def style_luxury(W, H, prod, name, price, contact, brand, sale):
    tz, foot = _zones(W, H, brand)
    gold = (214, 178, 102)
    canvas = vgrad(W, H, (12, 12, 16), (40, 36, 44))
    canvas = glow(canvas, W / 2, (H - tz) * 0.5, W * 0.7, gold, 0.42)
    canvas = place(canvas, prod, (0, int(H * 0.04), W, H - tz), shadow=0.45, reflect=True)
    d = ImageDraw.Draw(canvas)
    ly = H - tz + int(W * 0.015)
    d.line((W / 2 - W * 0.09, ly, W / 2 + W * 0.09, ly), fill=gold + (255,), width=3)
    text_block(canvas, ly + int(W * 0.02), H - foot, name, price, contact,
               title_font=SERIF_B, title_col=(245, 240, 230), upper=True,
               price_font=SERIF_B, price_col=gold, contact_col=(170, 160, 140))
    footer(canvas, brand, (150, 140, 120))
    return canvas


def style_sale(W, H, prod, name, price, contact, brand, sale):
    tz, foot = _zones(W, H, brand)
    canvas = vgrad(W, H, (226, 28, 42), (255, 118, 28))
    for i in range(-2, 6):
        x = i * W * 0.28
        canvas = overlay_shape(canvas, lambda d, x=x: d.polygon(
            [(x, 0), (x + W * 0.12, 0), (x + W * 0.12 + H * 0.45, H), (x + H * 0.45, H)], fill=(255, 255, 255, 20)))
    canvas = glow(canvas, W / 2, (H - tz) * 0.5, W * 0.6, (255, 226, 90), 0.55)
    canvas = place(canvas, prod, (0, int(H * 0.05), W, H - tz), shadow=0.4)
    canvas = overlay_shape(canvas, lambda d: d.rectangle((0, H - tz, W, H), fill=(28, 8, 12, 190)))
    text_block(canvas, H - tz, H - foot, name, price, contact,
               title_font=SANS_B, title_col=(255, 255, 255), upper=True,
               price_font=SANS_B, price_col=None, pill_col=(255, 214, 10), pill_text=(60, 10, 14),
               contact_col=(255, 220, 200))
    footer(canvas, brand, (255, 215, 190))
    r = W * 0.14
    badge(canvas, sale or "CHEGIRMA", W - r * 1.2, r * 1.25, r)
    return canvas


STYLE_FUNCS = {"minimal": style_minimal, "color": style_color, "luxury": style_luxury, "sale": style_sale}


def render(style, fmt, img, name="", price="", contact="", brand="", sale="CHEGIRMA") -> bytes:
    W, H = FORMATS.get(fmt, FORMATS["portrait"])
    prod = prep(img)
    canvas = STYLE_FUNCS.get(style, style_color)(W, H, prod, name, price, contact, brand, sale)
    return to_jpeg(canvas)


def render_free(img) -> bytes:
    """Oq fon, mahsulot tiniq, yengil soya."""
    W = H = 1080
    canvas = Image.new("RGBA", (W, H), (255, 255, 255, 255))
    canvas = place(canvas, prep(img), (0, 0, W, H), shadow=0.22, scale=0.86)
    return to_jpeg(canvas)
