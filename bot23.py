import asyncio
import io
import os
import re
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, Message,
)
from PIL import Image, ImageColor, ImageDraw, ImageEnhance, ImageFilter, ImageFont
from rembg import new_session, remove

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
DB_PATH = os.environ.get("DB_PATH", "bot.db")
SIZE = 1080

bot = Bot(TOKEN)
dp = Dispatcher()


class S(StatesGroup):
    free_photo = State()
    premium_photo = State()
    premium_text = State()


class A(StatesGroup):
    edit = State()
    give = State()
    bc = State()


# ---------- Sozlamalar (admin o'zgartiradi) ----------
DEFAULTS = {
    "welcome": "Salom! Mahsulot rasmingizni professional qilib beraman.\n\nRejimni tanlang:",
    "btn_free": "🆓 Oddiy (oq fon)",
    "btn_premium": "⭐ Premium (reklama post)",
    "btn_price": "💰 Narxlar",
    "ask_text": "Endi nomi va narxini yozing.\nMasalan: Sumka - 150 000 so'm",
    "premium_end": "Premium limitingiz tugadi.\n\nSotib olish uchun admin bilan bog'laning.",
    "price_info": "Premium narxi: 10 000 so'm / 1 ta rasm.\nTo'lov uchun admin bilan bog'laning.",
    "free_credits": "3",
    "color_top": "#FFF0E1",
    "color_bottom": "#FFBE96",
    "color_panel": "#1E1E1E",
    "color_price": "#FFBE3C",
}

LABELS = {
    "welcome": "Salomlashuv matni",
    "btn_free": "Oddiy tugma nomi",
    "btn_premium": "Premium tugma nomi",
    "btn_price": "Narxlar tugma nomi",
    "ask_text": "Nom/narx so'rash matni",
    "premium_end": "Limit tugagan matni",
    "price_info": "Narx haqida ma'lumot",
    "free_credits": "Yangi foydalanuvchiga bepul premium soni",
    "color_top": "Fon rangi (tepa)",
    "color_bottom": "Fon rangi (past)",
    "color_panel": "Matn paneli rangi",
    "color_price": "Narx matni rangi",
}

CATS = {
    "texts": ("📝 Matnlar", ["welcome", "btn_free", "btn_premium", "btn_price", "ask_text", "premium_end"]),
    "price": ("💰 Narx va limit", ["price_info", "free_credits"]),
    "design": ("🎨 Dizayn ranglari", ["color_top", "color_bottom", "color_panel", "color_price"]),
}

db = sqlite3.connect(DB_PATH)
db.execute("CREATE TABLE IF NOT EXISTS users (id INTEGER PRIMARY KEY, credits INTEGER)")
db.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
for col in ("made_free", "made_premium"):
    try:
        db.execute(f"ALTER TABLE users ADD COLUMN {col} INTEGER DEFAULT 0")
    except sqlite3.OperationalError:
        pass
db.commit()


def get_setting(key: str) -> str:
    row = db.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
    return row[0] if row else DEFAULTS[key]


def set_setting(key: str, value: str):
    db.execute("INSERT OR REPLACE INTO settings VALUES (?, ?)", (key, value))
    db.commit()


def get_credits(uid: int) -> int:
    row = db.execute("SELECT credits FROM users WHERE id=?", (uid,)).fetchone()
    if row is None:
        start = int(get_setting("free_credits"))
        db.execute("INSERT INTO users (id, credits) VALUES (?, ?)", (uid, start))
        db.commit()
        return start
    return row[0]


def use_credit(uid: int):
    db.execute("UPDATE users SET credits = credits - 1, made_premium = made_premium + 1 WHERE id=?", (uid,))
    db.commit()


def count_free(uid: int):
    get_credits(uid)
    db.execute("UPDATE users SET made_free = made_free + 1 WHERE id=?", (uid,))
    db.commit()


def get_colors() -> dict:
    return {k: ImageColor.getrgb(get_setting("color_" + k)) for k in ("top", "bottom", "panel", "price")}


# ---------- Rasm ishlash ----------
def font(size: int):
    for name in ("DejaVuSans-Bold.ttf", "arialbd.ttf", "Arial.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


SESSION = new_session("u2netp")  # yengil model


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
    prod = fit(img, int(SIZE * 0.85))
    canvas = Image.new("RGBA", (SIZE, SIZE), "white")
    canvas.paste(prod, ((SIZE - prod.width) // 2, (SIZE - prod.height) // 2), prod)
    canvas = canvas.convert("RGB")
    canvas = ImageEnhance.Sharpness(canvas).enhance(1.6)
    canvas = ImageEnhance.Contrast(canvas).enhance(1.1)
    return to_bytes(canvas)


def make_premium(img: Image.Image, name: str, price: str, colors: dict) -> bytes:
    prod = fit(img, int(SIZE * 0.62))

    canvas = Image.new("RGB", (SIZE, SIZE))
    top, bottom = colors["top"], colors["bottom"]
    d = ImageDraw.Draw(canvas)
    for y in range(SIZE):
        t = y / SIZE
        d.line([(0, y), (SIZE, y)],
               fill=tuple(int(top[i] + (bottom[i] - top[i]) * t) for i in range(3)))
    canvas = canvas.convert("RGBA")

    x = (SIZE - prod.width) // 2
    y = int(SIZE * 0.12)
    shadow = Image.new("RGBA", canvas.size, (0, 0, 0, 0))
    alpha = prod.split()[3].point(lambda p: int(p * 0.35))
    shadow.paste((0, 0, 0, 255), (x + 15, y + 25), alpha)
    canvas = Image.alpha_composite(canvas, shadow.filter(ImageFilter.GaussianBlur(18)))
    canvas.paste(prod, (x, y), prod)

    draw = ImageDraw.Draw(canvas)
    draw.rounded_rectangle((60, SIZE - 260, SIZE - 60, SIZE - 60), 40, fill=tuple(colors["panel"]) + (235,))
    draw.text((SIZE // 2, SIZE - 195), name, font=font(60), fill="white", anchor="mm")
    draw.text((SIZE // 2, SIZE - 120), price, font=font(70), fill=tuple(colors["price"]), anchor="mm")
    return to_bytes(canvas)


# ---------- Umumiy ----------
def btn(text: str, data: str) -> InlineKeyboardButton:
    return InlineKeyboardButton(text=text, callback_data=data)


def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS


def menu():
    return InlineKeyboardMarkup(inline_keyboard=[
        [btn(get_setting("btn_free"), "free")],
        [btn(get_setting("btn_premium"), "premium")],
        [btn(get_setting("btn_price"), "price")],
    ])


@dp.message(CommandStart())
async def start(m: Message, state: FSMContext):
    await state.clear()
    c = get_credits(m.from_user.id)
    await m.answer(f"{get_setting('welcome')}\n\n⭐ Premium qolgan: {c} ta", reply_markup=menu())


@dp.message(Command("id"))
async def my_id(m: Message):
    await m.answer(f"Sizning ID: {m.from_user.id}")


@dp.message(Command("cancel"))
async def cancel(m: Message, state: FSMContext):
    await state.clear()
    await m.answer("Bekor qilindi. /start")


@dp.callback_query(F.data == "price")
async def show_price(c: CallbackQuery):
    await c.message.answer(get_setting("price_info"))
    await c.answer()


@dp.callback_query(F.data == "free")
async def pick_free(c: CallbackQuery, state: FSMContext):
    await state.set_state(S.free_photo)
    await c.message.answer("Mahsulot rasmini yuboring 📷")
    await c.answer()


@dp.callback_query(F.data == "premium")
async def pick_premium(c: CallbackQuery, state: FSMContext):
    if get_credits(c.from_user.id) <= 0:
        await c.message.answer(get_setting("premium_end"))
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
    count_free(m.from_user.id)
    await wait.delete()
    await m.answer_photo(BufferedInputFile(result, "free.jpg"), caption="Tayyor ✅")
    await state.clear()


@dp.message(S.premium_photo, F.photo)
async def got_premium_photo(m: Message, state: FSMContext):
    await state.update_data(file=await download(m))
    await state.set_state(S.premium_text)
    await m.answer(get_setting("ask_text"))


@dp.message(S.premium_text, F.text)
async def do_premium(m: Message, state: FSMContext):
    if get_credits(m.from_user.id) <= 0:
        await m.answer(get_setting("premium_end"))
        await state.clear()
        return
    data = (await state.get_data())["file"]
    name, _, price = m.text.partition("-")
    wait = await m.answer("⏳ Reklama rasmi tayyorlanmoqda...")
    img = await cutout(data)
    result = await asyncio.to_thread(make_premium, img, name.strip(), price.strip(), get_colors())
    use_credit(m.from_user.id)
    await wait.delete()
    left = get_credits(m.from_user.id)
    await m.answer_photo(BufferedInputFile(result, "premium.jpg"),
                         caption=f"Tayyor ✅ Premium qoldi: {left} ta")
    await state.clear()


# ---------- ADMIN PANEL ----------
def admin_menu():
    rows = [[btn(title, f"cat:{k}")] for k, (title, _) in CATS.items()]
    rows += [
        [btn("🎁 Kredit berish", "give")],
        [btn("📊 Statistika", "stats")],
        [btn("📢 Xabar yuborish", "bc")],
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


@dp.message(Command("admin"))
async def admin(m: Message, state: FSMContext):
    await state.clear()
    if not is_admin(m.from_user.id):
        await m.answer("Ruxsat yo'q.")
        return
    await m.answer("🔧 Admin panel", reply_markup=admin_menu())


@dp.callback_query(F.data == "admin_home")
async def admin_home(c: CallbackQuery):
    if is_admin(c.from_user.id):
        await c.message.answer("🔧 Admin panel", reply_markup=admin_menu())
    await c.answer()


@dp.callback_query(F.data.startswith("cat:"))
async def admin_cat(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    key = c.data.split(":", 1)[1]
    title, keys = CATS[key]
    rows = [[btn(LABELS[k], f"edit:{k}")] for k in keys]
    rows.append([btn("⬅️ Orqaga", "admin_home")])
    await c.message.answer(title, reply_markup=InlineKeyboardMarkup(inline_keyboard=rows))
    await c.answer()


@dp.callback_query(F.data.startswith("edit:"))
async def admin_edit(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    key = c.data.split(":", 1)[1]
    await state.set_state(A.edit)
    await state.update_data(key=key)
    hint = ""
    if key.startswith("color_"):
        hint = "\n\nRangni #RRGGBB ko'rinishida yozing, masalan #FF8800"
    if key == "free_credits":
        hint = "\n\nFaqat raqam yozing, masalan 3"
    await c.message.answer(f"{LABELS[key]}\n\nHozirgi qiymat:\n{get_setting(key)}{hint}\n\nYangisini yozing (bekor qilish: /cancel)")
    await c.answer()


@dp.message(A.edit, F.text)
async def admin_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    key = (await state.get_data())["key"]
    val = m.text.strip()
    if key == "free_credits" and not val.isdigit():
        return await m.answer("Faqat raqam yozing.")
    if key.startswith("color_") and not re.fullmatch(r"#[0-9a-fA-F]{6}", val):
        return await m.answer("Rang noto'g'ri. Masalan: #FF8800")
    set_setting(key, val)
    await state.clear()
    await m.answer("✅ Saqlandi", reply_markup=admin_menu())


@dp.callback_query(F.data == "give")
async def admin_give(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await state.set_state(A.give)
    await c.message.answer("Foydalanuvchi ID va premium sonini yozing.\nMasalan: 123456789 5\n\n(ID ni foydalanuvchi botga /id yozib bilib oladi)")
    await c.answer()


@dp.message(A.give, F.text)
async def admin_give_do(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    try:
        uid, amount = (int(x) for x in m.text.split())
    except ValueError:
        return await m.answer("Format noto'g'ri. Masalan: 123456789 5")
    get_credits(uid)
    db.execute("UPDATE users SET credits = credits + ? WHERE id=?", (amount, uid))
    db.commit()
    await state.clear()
    await m.answer(f"✅ {uid} ga {amount} ta premium qo'shildi.", reply_markup=admin_menu())
    try:
        await bot.send_message(uid, f"🎁 Sizga {amount} ta premium berildi!")
    except Exception:
        pass


@dp.callback_query(F.data == "stats")
async def admin_stats(c: CallbackQuery):
    if not is_admin(c.from_user.id):
        return await c.answer()
    users, free, prem = db.execute(
        "SELECT COUNT(*), COALESCE(SUM(made_free),0), COALESCE(SUM(made_premium),0) FROM users").fetchone()
    await c.message.answer(
        f"📊 Statistika\n\n👥 Foydalanuvchilar: {users}\n🆓 Oddiy rasmlar: {free}\n⭐ Premium rasmlar: {prem}")
    await c.answer()


@dp.callback_query(F.data == "bc")
async def admin_bc(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    await state.set_state(A.bc)
    await c.message.answer("Hammaga yuboriladigan xabar matnini yozing (bekor qilish: /cancel)")
    await c.answer()


@dp.message(A.bc, F.text)
async def admin_bc_text(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    await state.update_data(text=m.text)
    await m.answer(f"Shu xabar hammaga yuboriladi:\n\n{m.text}",
                   reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                       [btn("✅ Yuborish", "bc_yes"), btn("❌ Bekor", "bc_no")]]))


@dp.callback_query(F.data == "bc_no")
async def bc_no(c: CallbackQuery, state: FSMContext):
    await state.clear()
    await c.message.answer("Bekor qilindi.", reply_markup=admin_menu())
    await c.answer()


@dp.callback_query(F.data == "bc_yes")
async def bc_yes(c: CallbackQuery, state: FSMContext):
    if not is_admin(c.from_user.id):
        return await c.answer()
    text = (await state.get_data()).get("text")
    await state.clear()
    await c.answer()
    if not text:
        return
    ids = [r[0] for r in db.execute("SELECT id FROM users").fetchall()]
    ok = bad = 0
    for uid in ids:
        try:
            await bot.send_message(uid, text)
            ok += 1
        except Exception:
            bad += 1
        await asyncio.sleep(0.05)
    await c.message.answer(f"📢 Yuborildi: {ok}, yetmadi: {bad}", reply_markup=admin_menu())


@dp.message()
async def fallback(m: Message):
    await m.answer("Boshlash uchun /start bosing.")


async def main():
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())
