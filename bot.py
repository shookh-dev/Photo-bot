import asyncio
import io
import os
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from rembg import new_session, remove

TOKEN = os.environ["BOT_TOKEN"]
FREE_PREMIUM_CREDITS = 3
SIZE = 1080

bot = Bot(TOKEN)
dp = Dispatcher()


class S(StatesGroup):
    free_photo = State()
    premium_photo = State()
    premium_text = State()


# ---------- Baza ----------
db = sqlite3.connect("bot.db")
db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, credits INTEGER)")
db.commit()


def get_credits(uid: int) -> int:
    row = db.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        db.execute("INSERT INTO users VALUES (?, ?)", (uid, FREE_PREMIUM_CREDITS))
        db.commit()
        return FREE_PREMIUM_CREDITS
    return row[0]


def use_credit(uid: int):
    db.execute("UPDATE users SET credits = credits - 1 WHERE id=?", (uid,))
    db.commit()


# ---------- Rasm ishlash ----------
def font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


SESSION = new_session("u2netp")  # yengil model, kam xotira ishlatadi


def _cutout_sync(data: bytes) -> Image.Image:
    img = remove(Image.open(io.BytesIO(data)).convert("RGBA"), session=SESSION)
    bbox = img.getbbox()
    return img.crop(bbox) if bbox else img


async def cutout(data: bytes) -> Image.Image:
    return await asyncio.to_thread(_cutout_sync, data)


def fit(img: Image.Image, box: int) -> Image.Image:
    img = img.copy()
    img.thumbnail((box, box))
    return img


def to_bytes(img: Image.Image) -> bytes:
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=95)
    return out.getvalue()


def make_free(img: Image.Image) -> bytes:
    """Oq fon + tiniqlashtirish."""
    prod = fit(img, int(SIZE * 0.85))
    canvas = Image.new("RGBA", (SIZE, SIZE), "white")
    canvas.paste(prod, ((SIZE - prod.width) // 2, (SIZE - prod.height) // 2), prod)
    canvas = canvas.convert("RGB")
    canvas = ImageEnhance.Sharpness(canvas).enhance(1.6)
    canvas = ImageEnhance.Contrast(canvas).enhance(1.1)
    return to_bytes(canvas)


def make_premium(img: Image.Image, name: str, price: str) -> bytes:
    """Gradient fon + soya + nom va narx."""
    prod = fit(img, int(SIZE * 0.62))

    # Gradient fon
    canvas = Image.new("RGB", (SIZE, SIZE))
    top, bottom = (255, 240, 225), (255, 190, 150)
    d = ImageDraw.Draw(canvas)
    for y in range(SIZE):
        t = y / SIZE
        d.line([(0, y), (SIZE, y)],
               fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    canvas = canvas.convert("RGBA")

    # Soya
    x = (SIZE - prod.width) // 2
    y = int(SIZE * 0.12)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    alpha = prod.split()[3].point(lambda p: int(p * 0.35))
    shadow.paste((0, 0, 0, 255), (x + 15, y + 25), alpha)
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(18)))
    canvas.paste(prod, (x, y), prod)

    # Matn paneli
    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((60, SIZE - 260, SIZE - 60, SIZE - 60), 40, fill=(30, 30, 30, 235))
    draw.text((SIZE // 2, SIZE - 195), name, font=font(60), fill="white", anchor="mm")
    draw.text((SIZE // 2, SIZE - 120), price, font=font(70), fill=(255, 190, 60), anchor="mm")
    return to_bytes(canvas)


# ---------- Bot ----------
def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🆓 Oddiy (oq fon)", callback_data="free")],
        [InlineKeyboardButton(text="⭐ Premium (reklama post)", callback_data="premium")],
    ])


@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    c = get_credits(m.from_user.id)
    await m.answer(
        f"Salom! Mahsulot rasmingizni professional qilib beraman.\n\n"
        f"⭐ Premium qolgan: {c} ta\nRejimni tanlang:",
        reply_markup=menu(),
    )


@dp.callback_query(F.data == "free")
async def pick_free(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.free_photo)
    await c.message.answer("Mahsulot rasmini yuboring 📷")
    await c.answer()


@dp.callback_query(F.data == "premium")
async def pick_premium(c: CallbackQuery, state: FSMContext):
    if get_credits(c.from_user.id) <= 0:
        await c.message.answer("Premium limitingiz tugadi. To'lov tizimi tez orada qo'shiladi.")
    else:
        await state.set_state(S.premium_photo)
        await c.message.answer("Mahsulot rasmini yuboring 📷")
    await c.answer()


async def download(m: Message) -> bytes:
    buf = io.BytesIO()
    await bot.download(m.photo[-1], destination=buf)
    return buf.getvalue()


@dp.message(S.free_photo, F.photo)
async def do_free(m: Message, state: FSMContext):
    wait = await m.answer("⏳ Tayyorlanmoqda...")
    data = await download(m)
    img = await cutout(data)
    result = await asyncio.to_thread(make_free, img)
    await wait.delete()
    await m.answer_photo(BufferedInputFile(result, "free.jpg"), caption="Tayyor ✅")
    await state.clear()


@dp.message(S.premium_photo, F.photo)
async def got_premium_photo(m: Message, state: FSMContext):
    await state.update_data(file=await download(m))
    await state.set_state(S.premium_text)
    await m.answer("Endi nomi va narxini yozing.\nMasalan: Sumka - 150 000 so'm")


@dp.message(S.premium_text, F.text)
async def do_premium(m: Message, state: FSMContext):
    if get_credits(m.from_user.id) <= 0:
        await m.answer("Premium limitingiz tugadi.")
        await state.clear()
        return
    data = (await state.get_data())["file"]
    name, _, price = m.text.partition("-")
    wait = await m.answer("⏳ Reklama rasmi tayyorlanmoqda...")
    img = await cutout(data)
    result = await asyncio.to_thread(make_premium, img, name.strip(), price.strip())
    use_credit(m.from_user.id)
    await wait.delete()
    left = get_credits(m.from_user.id)
    await m.answer_photo(BufferedInputFile(result, "premium.jpg"),
                         caption=f"Tayyor ✅ Premium qoldi: {left} ta")
    await state.clear()


@dp.message()
async def fallback(m: Message):
    await m.answer("Boshlash uchun /start bosing.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
