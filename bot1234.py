import asyncio
import io
import os
import re
import sqlite3

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    BufferedInputFile, CallbackQuery, InlineKeyboardButton,
    InlineKeyboardMarkup, InputMediaPhoto, Message,
)
from PIL import Image, ImageFilter
from rembg import new_session, remove

import design

TOKEN = os.environ["BOT_TOKEN"]
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(" ", "").split(",") if x.isdigit()}
DB_PATH = os.environ.get("DB_PATH", "bot.db")
MODEL = os.environ.get("REMBG_MODEL", "u2netp")
MAX_REDO = 10

bot = Bot(TOKEN)
dp = Dispatcher()
GEN_SEM = asyncio.Semaphore(2)  # bir vaqtda ko'pi bilan 2 ta rasm (xotirani asrash)


class S(StatesGroup):
    free_photo = State()
    premium_photo = State()
    premium_text = State()
    result = State()


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
    "ask_text": "Endi nomi va narxini yozing.\nMasalan: Sumka - 150 000 so'm\n\nXohlasangiz telefon ham qo'shing:\nSumka - 150 000 so'm - +998 90 123 45 67",
    "premium_end": "Premium limitingiz tugadi.\n\nSotib olish uchun admin bilan bog'laning.",
    "price_info": "Premium narxi: 10 000 so'm / 1 ta rasm.\nTo'lov uchun admin bilan bog'laning.",
    "free_credits": "3",
    "brand": "",
    "sale_text": "CHEGIRMA",
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
    "brand": "Rasm tagidagi yozuv (masalan @do'koningiz)",
    "sale_text": "Chegirma belgisi yozuvi",
}

CATS = {
    "texts": ("📝 Matnlar", ["welcome", "btn_free", "btn_premium", "btn_price", "ask_text", "premium_end"]),
    "price": ("💰 Narx va limit", ["price_info", "free_credits"]),
    "design": ("🎨 Dizayn yozuvlari", ["brand", "sale_text"]),
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


# ---------- Fonni olib tashlash ----------
SESSION = new_session(MODEL)  # u2netp: yengil. Kuchliroq: isnet-general-use


def _cutout_sync(data: bytes) -> Image.Image:
    img = remove(Image.open(io.BytesIO(data)).convert("RGBA"), session=SESSION)
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    # chekkalarni tozalash: oq/qora "hoshiya"ni olib tashlaydi
    a = img.getchannel("A").filter(ImageFilter.MinFilter(3)).filter(ImageFilter.GaussianBlur(1.0))
    img.putalpha(a)
    return img


async def cutout(data: bytes) -> Image.Image:
    return await asyncio.to_thread(_cutout_sync, data)


def png_bytes(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def parse_text(text: str):
    """'Nom - narx - telefon' ni ajratadi."""
    text = text.strip()
    parts = [p.strip() for p in re.split(r"\s+[-–—|]\s+", text) if p.strip()]
    if len(parts) == 1 and "-" in text:
        a, _, b = text.rpartition("-")
        parts = [a.strip(), b.strip()]
    name = parts[0] if parts else ""
    price = parts[1] if len(parts) > 1 else ""
    contact = parts[2] if len(parts) > 2 else ""
    return name[:60], price[:30], contact[:40]


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


def result_kb(style: str, fmt: str):
    def mark(label, current):
        return ("✔ " if current else "") + label

    styles = [btn(mark(design.STYLE_NAMES[s], s == style), f"re:s:{s}") for s in design.STYLES]
    fmts = [btn(mark(design.FORMAT_NAMES[f], f == fmt), f"re:f:{f}") for f in design.FORMATS]
    return InlineKeyboardMarkup(inline_keyboard=[
        styles[:2], styles[2:], fmts, [btn("📥 Sifatli fayl sifatida", "dl")],
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
    try:
        data = await download(m)
        async with GEN_SEM:
            img = await cutout(data)
            result = await asyncio.to_thread(design.render_free, img)
    except Exception:
        await wait.delete()
        await m.answer("Xatolik bo'ldi, rasmni qayta yuboring.")
        return
    count_free(m.from_user.id)
    await wait.delete()
    await m.answer_photo(BufferedInputFile(result, "free.jpg"),
                         caption="Tayyor ✅\n\nReklama dizayni uchun /start → ⭐ Premium")
    await state.clear()


@dp.message(S.premium_photo, F.photo)
async def got_premium_photo(m: Message, state: FSMContext):
    await state.update_data(file=await download(m))
    await state.set_state(S.premium_text)
    await m.answer(get_setting("ask_text"))


@dp.message(StateFilter(S.free_photo, S.premium_photo))
async def need_photo(m: Message):
    await m.answer("Iltimos, mahsulot rasmini oddiy rasm sifatida yuboring (fayl emas) 📷")


@dp.message(S.premium_text, F.text)
async def do_premium(m: Message, state: FSMContext):
    if get_credits(m.from_user.id) <= 0:
        await m.answer(get_setting("premium_end"))
        await state.clear()
        return
    data = (await state.get_data()).get("file")
    if not data:
        await m.answer("Rasm topilmadi. /start dan qaytadan boshlang.")
        await state.clear()
        return
    name, price, contact = parse_text(m.text)
    style, fmt = "color", "portrait"
    wait = await m.answer("⏳ Reklama rasmi tayyorlanmoqda...")
    try:
        async with GEN_SEM:
            cut = await cutout(data)
            result = await asyncio.to_thread(
                design.render, style, fmt, cut, name, price, contact,
                get_setting("brand"), get_setting("sale_text"))
    except Exception:
        await wait.delete()
        await m.answer("Xatolik bo'ldi, qayta urinib ko'ring. Premium hisobingizdan yechilmadi.")
        return
    use_credit(m.from_user.id)
    await state.set_state(S.result)
    await state.update_data(file=None, cut=png_bytes(cut), name=name, price=price,
                            contact=contact, style=style, fmt=fmt, redo=0, last=result)
    await wait.delete()
    left = get_credits(m.from_user.id)
    await m.answer_photo(
        BufferedInputFile(result, "reklama.jpg"),
        caption=f"Tayyor ✅ Premium qoldi: {left} ta\n\nUslub va formatni pastdagi tugmalar bilan o'zgartiring (bepul).",
        reply_markup=result_kb(style, fmt))


@dp.callback_query(F.data.startswith("re:"))
async def restyle(c: CallbackQuery, state: FSMContext):
    d = await state.get_data()
    if not d.get("cut"):
        return await c.answer("Bu rasm eskirgan. /start dan qaytadan boshlang.", show_alert=True)
    if d.get("redo", 0) >= MAX_REDO:
        return await c.answer("Bu rasm uchun o'zgartirish limiti tugadi.", show_alert=True)
    try:
        _, kind, val = c.data.split(":")
    except ValueError:
        return await c.answer()
    style = val if kind == "s" and val in design.STYLES else d["style"]
    fmt = val if kind == "f" and val in design.FORMATS else d["fmt"]
    if style == d["style"] and fmt == d["fmt"]:
        return await c.answer()
    await c.answer("⏳ Tayyorlanmoqda...")
    cut = Image.open(io.BytesIO(d["cut"])).convert("RGBA")
    async with GEN_SEM:
        result = await asyncio.to_thread(
            design.render, style, fmt, cut, d["name"], d["price"], d["contact"],
            get_setting("brand"), get_setting("sale_text"))
    await state.update_data(style=style, fmt=fmt, redo=d.get("redo", 0) + 1, last=result)
    media = InputMediaPhoto(media=BufferedInputFile(result, "reklama.jpg"),
                            caption="Tayyor ✅ Uslub va formatni o'zgartirishingiz mumkin (bepul).")
    try:
        await c.message.edit_media(media=media, reply_markup=result_kb(style, fmt))
    except Exception:
        await c.message.answer_photo(BufferedInputFile(result, "reklama.jpg"), reply_markup=result_kb(style, fmt))


@dp.callback_query(F.data == "dl")
async def download_file(c: CallbackQuery, state: FSMContext):
    last = (await state.get_data()).get("last")
    if not last:
        return await c.answer("Bu rasm eskirgan. /start dan qaytadan boshlang.", show_alert=True)
    await c.message.answer_document(BufferedInputFile(last, "reklama.jpg"))
    await c.answer()


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
    if key == "free_credits":
        hint = "\n\nFaqat raqam yozing, masalan 3"
    if key == "brand":
        hint = "\n\nO'chirish uchun - (chiziq) yozing"
    cur = get_setting(key) or "(bo'sh)"
    await c.message.answer(f"{LABELS[key]}\n\nHozirgi qiymat:\n{cur}{hint}\n\nYangisini yozing (bekor qilish: /cancel)")
    await c.answer()


@dp.message(A.edit, F.text)
async def admin_save(m: Message, state: FSMContext):
    if not is_admin(m.from_user.id):
        return
    key = (await state.get_data())["key"]
    val = m.text.strip()
    if key == "free_credits" and not val.isdigit():
        return await m.answer("Faqat raqam yozing.")
    if key == "brand" and val == "-":
        val = ""
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
