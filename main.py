import asyncio
import json
import logging
import os
import re
from datetime import date

from aiogram import Bot, Dispatcher, F, Router
from aiogram.filters import Command
from aiogram.types import (
    BufferedInputFile,
    CallbackQuery,
    Document,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    Message,
    ReplyKeyboardMarkup,
)
from dotenv import load_dotenv

load_dotenv()

import database
from ivasms import IVASMSClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

_poll_event = asyncio.Event()

# ─── Config ───────────────────────────────────────────────────────────────────

def load_config() -> dict:
    config_path = os.path.join(os.path.dirname(__file__), "config.json")
    if os.path.exists(config_path):
        try:
            with open(config_path) as f:
                data = json.load(f)
            logger.info("Config dimuat dari config.json")
            return data
        except Exception as e:
            logger.warning(f"Gagal baca config.json: {e}")
    return {}


_cfg = load_config()


def _get(key: str, default: str = "") -> str:
    val = _cfg.get(key, "")
    if val:
        return str(val)
    return os.getenv(key, default)


BOT_TOKEN = _get("BOT_TOKEN")
ADMIN_CHAT_ID = int(_get("ADMIN_CHAT_ID", "0"))
POLL_INTERVAL_DEFAULT = int(_get("POLL_INTERVAL", "10"))

router = Router()

# ─── Helpers ──────────────────────────────────────────────────────────────────

def get_ivasms_cookies() -> str:
    saved = database.get_setting("ivasms_cookies")
    if saved:
        return saved
    from_cfg = _cfg.get("IVASMS_COOKIES", "")
    if from_cfg:
        if isinstance(from_cfg, dict) and len(from_cfg) > 0:
            return json.dumps(from_cfg)
        elif isinstance(from_cfg, str) and from_cfg.strip():
            return from_cfg
    return os.getenv("IVASMS_COOKIES", "")


def get_poll_interval() -> int:
    saved = database.get_setting("poll_interval")
    if saved:
        try:
            v = int(saved)
            if v >= 3:
                return v
        except Exception:
            pass
    return POLL_INTERVAL_DEFAULT


def is_poll_paused() -> bool:
    return database.get_setting("poll_paused") == "1"


def is_admin(user_id: int) -> bool:
    return ADMIN_CHAT_ID != 0 and user_id == ADMIN_CHAT_ID


async def deny(target) -> None:
    if isinstance(target, CallbackQuery):
        await target.answer("⛔ Akses ditolak!", show_alert=True)
    else:
        await target.answer("⛔ Akses ditolak!")


SEP = "─" * 28

# ─── Keyboards ────────────────────────────────────────────────────────────────

def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🚀  Gas Ambil Nomor!", callback_data="pick_count")],
        [
            InlineKeyboardButton(text="📦 Cek Stok", callback_data="status"),
            InlineKeyboardButton(text="🗑 Hapus Semua", callback_data="clear_numbers"),
        ],
    ])


def bottom_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(text="🚀 Gas Nomor"),
                KeyboardButton(text="⚡ Cek Sekarang"),
            ],
            [
                KeyboardButton(text="📦 Cek Stok"),
                KeyboardButton(text="📋 History OTP"),
            ],
            [
                KeyboardButton(text="📤 Upload Nomor"),
                KeyboardButton(text="📥 Export Nomor"),
            ],
            [
                KeyboardButton(text="🍪 Set Cookies"),
                KeyboardButton(text="⏸ Pause/Resume"),
            ],
            [
                KeyboardButton(text="🗑 Hapus Semua"),
            ],
        ],
        resize_keyboard=True,
        persistent=True,
    )

# ─── Parsing ──────────────────────────────────────────────────────────────────

def quality_label(q: str) -> str:
    return {
        "bio_lmb": "👑 Bio+LMB",
        "bio":     "✅ Bio",
        "lmb":     "🔵 LMB",
        "standard": "⚪ Std",
    }.get(q, q)


def quality_label_short(q: str) -> str:
    return {
        "bio_lmb": "Bio+LMB",
        "bio":     "Bio",
        "lmb":     "LMB",
        "standard": "Std",
    }.get(q, q)


def is_cekbio_file(content: str) -> bool:
    return "HASIL CEK BIO WHATSAPP" in content or "NOMOR DENGAN BIO" in content


def parse_cekbio_file(content: str) -> list[tuple[str, str]]:
    entries = []
    BIO_SECTION = r"\[\s*NOMOR DENGAN BIO"
    NOBIO_SECTION = r"\[NOMOR TANPA BIO"
    NOTDAFTAR_SECTION = r"\[\s*NOMOR TIDAK TERDAFTAR"

    bio_start = re.search(BIO_SECTION, content)
    nobio_start = re.search(NOBIO_SECTION, content)
    notdaftar_start = re.search(NOTDAFTAR_SECTION, content)

    bio_text = ""
    nobio_text = ""

    if bio_start:
        end = (
            nobio_start.start() if nobio_start
            else (notdaftar_start.start() if notdaftar_start else len(content))
        )
        bio_text = content[bio_start.start():end]

    if nobio_start:
        end = notdaftar_start.start() if notdaftar_start else len(content)
        nobio_text = content[nobio_start.start():end]

    phone_re = re.compile(r'\+\d{7,15}')
    lmb_re = re.compile(r'\(Low Meta Business\)', re.IGNORECASE)

    if bio_text:
        blocks = re.split(r'\[\d+\]', bio_text)
        for block in blocks:
            phones = phone_re.findall(block)
            if not phones:
                continue
            has_lmb = bool(lmb_re.search(block))
            bio_line = re.search(r'Bio:\s*(.+)', block)
            has_bio_text = bool(bio_line and bio_line.group(1).strip())
            if has_lmb and has_bio_text:
                q = "bio_lmb"
            elif has_lmb:
                q = "lmb"
            else:
                q = "bio"
            for phone in phones:
                entries.append((phone, q))

    if nobio_text:
        for line in nobio_text.splitlines():
            line = line.strip()
            phones = phone_re.findall(line)
            if not phones:
                continue
            has_lmb = bool(lmb_re.search(line))
            q = "lmb" if has_lmb else "standard"
            for phone in phones:
                entries.append((phone, q))

    return entries

# ─── Shared display helpers ───────────────────────────────────────────────────

async def _show_status(target):
    total = database.count_numbers()
    q = database.count_by_quality()
    cookies_set = bool(get_ivasms_cookies())
    paused = is_poll_paused()
    interval = get_poll_interval()

    ck_icon = "🟢" if cookies_set else "🔴"
    pl_icon = "⏸" if paused else "🟢"

    text = (
        f"<b>📦 Stok Nomor</b>\n"
        f"{SEP}\n"
        f"👑 Bio+LMB  : <b>{q.get('bio_lmb', 0)}</b> nomor\n"
        f"✅ Bio       : <b>{q.get('bio', 0)}</b> nomor\n"
        f"🔵 LMB       : <b>{q.get('lmb', 0)}</b> nomor\n"
        f"⚪ Standard  : <b>{q.get('standard', 0)}</b> nomor\n"
        f"{SEP}\n"
        f"📊 Total: <b>{total} nomor</b>\n\n"
        f"{ck_icon} Cookies: <b>{'Aktif' if cookies_set else 'Belum diset!'}</b>\n"
        f"{pl_icon} Polling: <b>{'PAUSE' if paused else f'Aktif ({interval}s)'}</b>"
    )
    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())
    else:
        await target.message.answer(text, parse_mode="HTML", reply_markup=main_menu_keyboard())


async def _show_history(target):
    otps = database.get_today_otps()
    if not otps:
        text = (
            f"<b>📋 History OTP Hari Ini</b>\n"
            f"{SEP}\n"
            f"Belum ada OTP yang masuk hari ini.\n"
            f"Sabar ya, gas terus! 💪"
        )
    else:
        lines = [f"<b>📋 History OTP — {len(otps)} masuk</b>\n{SEP}"]
        for o in otps:
            waktu = o["seen_at"][11:16] if o["seen_at"] else "?"
            lines.append(
                f"🕐 <i>{waktu}</i>  |  📱 <code>{o['phone_number']}</code>\n"
                f"🔑 OTP: <code>{o['otp_message']}</code>"
            )
        text = "\n\n".join(lines)

    if isinstance(target, Message):
        await target.answer(text, parse_mode="HTML")
    else:
        await target.message.answer(text, parse_mode="HTML")


async def _show_count_picker(target):
    count_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📦  3 Nomor", callback_data="pick_quality:3")],
        [InlineKeyboardButton(text="📦  5 Nomor", callback_data="pick_quality:5")],
        [InlineKeyboardButton(text="📦  10 Nomor", callback_data="pick_quality:10")],
        [InlineKeyboardButton(text="❌  Batal", callback_data="cancel_pick")],
    ])
    msg = f"<b>🎯 Mau ambil berapa nomor?</b>\n{SEP}\nPilih jumlahnya dulu bro:"
    if isinstance(target, Message):
        await target.answer(msg, parse_mode="HTML", reply_markup=count_kb)
    else:
        await target.message.answer(msg, parse_mode="HTML", reply_markup=count_kb)

# ─── Command Handlers ─────────────────────────────────────────────────────────

@router.message(Command("start"))
async def cmd_start(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    cookies_set = bool(get_ivasms_cookies())
    paused = is_poll_paused()
    interval = get_poll_interval()
    total = database.count_numbers()
    ck_icon = "🟢" if cookies_set else "🔴"
    pl_icon = "⏸" if paused else "🟢"
    await message.answer(
        f"<b>🤖 iVAS OTP Bot</b>\n"
        f"{SEP}\n\n"
        f"{ck_icon} <b>Cookies:</b>  {'Aktif ✓' if cookies_set else 'Belum diset!'}\n"
        f"{pl_icon} <b>Polling:</b>  {'PAUSE' if paused else f'Aktif tiap {interval}s'}\n"
        f"📱 <b>Stok:</b>     {total} nomor\n\n"
        f"{SEP}\n"
        f"<b>⚙️ Commands:</b>\n"
        f"  /setcookies  — Set cookies iVAS\n"
        f"  /setinterval — Ubah kecepatan cek\n"
        f"  /delnum      — Hapus satu nomor\n"
        f"  /history     — OTP hari ini\n"
        f"  /status      — Cek stok\n"
        f"{SEP}\n"
        f"⬇️ <i>Gunakan tombol di bawah</i>",
        parse_mode="HTML",
        reply_markup=bottom_keyboard(),
    )


@router.message(Command("setcookies"))
async def cmd_setcookies(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            f"<b>🍪 Set Cookies iVAS</b>\n"
            f"{SEP}\n"
            f"Cara pakai:\n"
            f"<code>/setcookies &lt;cookies&gt;</code>\n\n"
            f"<b>Cara ambil cookies (HP):</b>\n"
            f"1. Buka Chrome, login ke ivasms.com\n"
            f"2. Ketik di address bar:\n"
            f"<code>javascript:void(document.cookie)</code>\n"
            f"3. Copy semua teks → kirim ke sini\n\n"
            f"<b>Cara ambil (PC):</b>\n"
            f"DevTools (F12) → Application → Cookies",
            parse_mode="HTML",
        )
        return

    raw = parts[1].strip()
    try:
        parsed = json.loads(raw)
        if not isinstance(parsed, (dict, list)):
            raise ValueError
        cookies_str = json.dumps(parsed)
    except Exception:
        cookies_str = raw

    await message.answer("⏳ Lagi ngecek cookies ke iVAS SMS...")

    try:
        async with IVASMSClient(cookies_str) as client:
            ok = await client.login()
            if ok:
                updated = client.get_updated_cookies_str()
                if updated:
                    cookies_str = updated
    except Exception as e:
        logger.error(f"Error validating cookies: {e}")
        ok = False

    if not ok:
        await message.answer(
            f"<b>❌ Cookies Ditolak!</b>\n"
            f"{SEP}\n"
            f"Cookies ga valid atau udah expired.\n\n"
            f"Coba langkah ini:\n"
            f"1. Login ulang ke ivasms.com di Chrome\n"
            f"2. Ketik: <code>javascript:void(document.cookie)</code>\n"
            f"3. Copy hasilnya dan kirim ulang",
            parse_mode="HTML",
        )
        return

    database.set_setting("ivasms_cookies", cookies_str)
    _poll_event.set()
    await message.answer(
        f"<b>✅ Cookies Tersimpan!</b>\n"
        f"{SEP}\n"
        f"Cookies valid dan sudah disimpan.\n"
        f"Bot langsung gas polling iVAS! 🚀",
        parse_mode="HTML",
        reply_markup=bottom_keyboard(),
    )


@router.message(Command("setinterval"))
async def cmd_setinterval(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        current = get_poll_interval()
        await message.answer(
            f"<b>⏱ Interval Polling</b>\n"
            f"{SEP}\n"
            f"Sekarang: <b>{current} detik</b>\n\n"
            f"Cara ubah:\n"
            f"<code>/setinterval &lt;detik&gt;</code>\n"
            f"Minimal 3 detik. Contoh: <code>/setinterval 3</code>",
            parse_mode="HTML",
        )
        return

    try:
        val = int(parts[1].strip())
        if val < 3:
            await message.answer("⚠️ Minimal 3 detik bro!")
            return
    except ValueError:
        await message.answer("⚠️ Harus angka! Contoh: <code>/setinterval 3</code>", parse_mode="HTML")
        return

    database.set_setting("poll_interval", str(val))
    _poll_event.set()
    await message.answer(
        f"✅ Interval polling diubah ke <b>{val} detik</b>.",
        parse_mode="HTML",
    )


@router.message(Command("delnum"))
async def cmd_delnum(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return

    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer(
            f"<b>🗑 Hapus Nomor</b>\n"
            f"{SEP}\n"
            f"Cara pakai:\n"
            f"<code>/delnum &lt;nomor&gt;</code>\n"
            f"Contoh: <code>/delnum +628123456789</code>",
            parse_mode="HTML",
        )
        return

    number = parts[1].strip()
    deleted = database.delete_number(number)
    if deleted:
        await message.answer(
            f"✅ Nomor <code>{number}</code> berhasil dihapus.",
            parse_mode="HTML",
        )
    else:
        await message.answer(
            f"❌ Nomor <code>{number}</code> tidak ditemukan di stok.",
            parse_mode="HTML",
        )


@router.message(Command("history"))
async def cmd_history(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await _show_history(message)


@router.message(Command("addnum"))
async def cmd_addnum(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await message.answer(
        f"<b>📤 Upload Nomor</b>\n"
        f"{SEP}\n"
        f"Kirim file <b>.txt</b> sekarang!\n\n"
        f"2 format yang didukung:\n"
        f"1️⃣  Nomor doang (satu per baris)\n"
        f"2️⃣  File hasil cekbio — auto filter kualitas!\n\n"
        f"<i>Yang ga terdaftar WA langsung dibuang.</i>",
        parse_mode="HTML",
    )


@router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await _show_status(message)

# ─── Keyboard Button Handlers ─────────────────────────────────────────────────

@router.message(F.text == "🚀 Gas Nomor")
async def kb_get_numbers(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await _show_count_picker(message)


@router.message(F.text == "📦 Cek Stok")
async def kb_status(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await _show_status(message)


@router.message(F.text == "📋 History OTP")
async def kb_history(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await _show_history(message)


@router.message(F.text == "⚡ Cek Sekarang")
async def kb_poll_now(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    if is_poll_paused():
        await message.answer("⏸ Polling lagi di-pause bro. Resume dulu!")
        return
    if not get_ivasms_cookies():
        await message.answer("🔴 Cookies belum diset! Kirim /setcookies dulu.")
        return
    _poll_event.set()
    await message.answer("⚡ Gas! Lagi ngecek OTP ke iVAS sekarang...")


@router.message(F.text == "📥 Export Nomor")
async def kb_export(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    numbers = database.get_all_numbers_for_export()
    if not numbers:
        await message.answer("📭 Stok kosong bro, belum ada nomor!")
        return

    lines = [f"{num} [{quality_label_short(q)}]" for num, q in numbers]
    content = "\n".join(lines)
    today = date.today().strftime("%Y%m%d")
    filename = f"nomor_{today}_{len(numbers)}pcs.txt"
    await message.answer_document(
        document=BufferedInputFile(content.encode("utf-8"), filename=filename),
        caption=f"📥 Export selesai — <b>{len(numbers)} nomor</b>.",
        parse_mode="HTML",
    )


@router.message(F.text == "📤 Upload Nomor")
async def kb_upload(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await message.answer(
        f"<b>📤 Upload Nomor</b>\n"
        f"{SEP}\n"
        f"Kirim file <b>.txt</b> sekarang!\n\n"
        f"2 format yang didukung:\n"
        f"1️⃣  Nomor doang (satu per baris)\n"
        f"2️⃣  File hasil cekbio — auto filter!\n\n"
        f"<i>Yang ga terdaftar WA langsung dibuang.</i>",
        parse_mode="HTML",
    )


@router.message(F.text == "🗑 Hapus Semua")
async def kb_clear(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ya, Hapus Semua", callback_data="confirm_clear"),
            InlineKeyboardButton(text="❌ Batal", callback_data="cancel_clear"),
        ]
    ])
    await message.answer(
        f"<b>⚠️ Hapus Semua Nomor?</b>\n"
        f"{SEP}\n"
        f"Semua nomor di stok akan dihapus permanen.\n"
        f"<b>Tidak bisa di-undo!</b>",
        parse_mode="HTML",
        reply_markup=confirm_kb,
    )


@router.message(F.text == "🍪 Set Cookies")
async def kb_setcookies(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    await message.answer(
        f"<b>🍪 Set Cookies iVAS</b>\n"
        f"{SEP}\n"
        f"Cara pakai:\n"
        f"<code>/setcookies &lt;cookies&gt;</code>\n\n"
        f"<b>Cara ambil cookies (HP):</b>\n"
        f"1. Buka Chrome, login ke ivasms.com\n"
        f"2. Ketik di address bar:\n"
        f"<code>javascript:void(document.cookie)</code>\n"
        f"3. Copy semua teks → kirim dengan /setcookies\n\n"
        f"Bot otomatis cek validitas sebelum menyimpan. ✅",
        parse_mode="HTML",
    )


@router.message(F.text == "⏸ Pause/Resume")
async def kb_pause_resume(message: Message):
    if not is_admin(message.from_user.id):
        await deny(message)
        return
    paused = is_poll_paused()
    if paused:
        database.set_setting("poll_paused", "0")
        _poll_event.set()
        await message.answer(
            f"<b>▶️ Polling Di-resume!</b>\n"
            f"{SEP}\n"
            f"Bot balik gas ngecek OTP iVAS. 🚀",
            parse_mode="HTML",
            reply_markup=bottom_keyboard(),
        )
    else:
        database.set_setting("poll_paused", "1")
        await message.answer(
            f"<b>⏸ Polling Di-pause!</b>\n"
            f"{SEP}\n"
            f"Bot berhenti ngecek iVAS sementara.\n"
            f"Tekan <b>⏸ Pause/Resume</b> lagi untuk nyalain balik.",
            parse_mode="HTML",
            reply_markup=bottom_keyboard(),
        )

# ─── Document Handler ─────────────────────────────────────────────────────────

@router.message(F.document)
async def handle_document(message: Message, bot: Bot):
    if not is_admin(message.from_user.id):
        await deny(message)
        return

    doc: Document = message.document
    if not doc.file_name.endswith(".txt"):
        await message.answer("⚠️ Harap kirim file <b>.txt</b> ya bro.", parse_mode="HTML")
        return

    await message.answer("⏳ Lagi diproses...")

    file = await bot.get_file(doc.file_id)
    downloaded = await bot.download_file(file.file_path)
    content = downloaded.read().decode("utf-8", errors="ignore")

    if is_cekbio_file(content):
        entries = parse_cekbio_file(content)
        if not entries:
            await message.answer(
                f"<b>⚠️ File Cekbio Kosong</b>\n"
                f"{SEP}\n"
                f"File cekbio terdeteksi tapi tidak ada nomor\n"
                f"yang terdaftar di WhatsApp. Coba file lain!",
                parse_mode="HTML",
                reply_markup=main_menu_keyboard(),
            )
            return

        added, skipped = database.add_numbers_with_quality(entries)
        q = database.count_by_quality()
        total = database.count_numbers()

        await message.answer(
            f"<b>✅ File Cekbio Berhasil Diproses!</b>\n"
            f"{SEP}\n"
            f"➕ Nomor masuk   : <b>{added}</b>\n"
            f"⏭ Skip duplikat : <b>{skipped}</b>\n\n"
            f"<b>📦 Stok Sekarang ({total} total):</b>\n"
            f"  👑 Bio+LMB  : {q.get('bio_lmb', 0)}\n"
            f"  ✅ Bio       : {q.get('bio', 0)}\n"
            f"  🔵 LMB       : {q.get('lmb', 0)}\n"
            f"  ⚪ Standard  : {q.get('standard', 0)}\n\n"
            f"<i>Yang ga terdaftar WA sudah dibuang otomatis.</i>",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
    else:
        lines = [line.strip() for line in content.splitlines() if line.strip()]
        added, skipped = database.add_numbers(lines, quality="standard")
        await message.answer(
            f"<b>✅ Nomor Berhasil Masuk!</b>\n"
            f"{SEP}\n"
            f"➕ Nomor masuk   : <b>{added}</b>\n"
            f"⏭ Skip duplikat : <b>{skipped}</b>\n"
            f"📊 Total stok    : <b>{database.count_numbers()} nomor</b>",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )

# ─── Callback Handlers ────────────────────────────────────────────────────────

@router.callback_query(F.data == "pick_count")
async def cb_pick_count(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()
    await _show_count_picker(callback)


@router.callback_query(F.data.startswith("pick_quality:"))
async def cb_pick_quality(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()

    count = callback.data.split(":")[1]
    q = database.count_by_quality()
    bio_lmb_count = q.get("bio_lmb", 0) + q.get("bio", 0) + q.get("lmb", 0)
    lmb_count = q.get("lmb", 0) + q.get("bio_lmb", 0)
    total = database.count_numbers()

    filter_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(
            text=f"👑  Sultan — Bio/LMB ({bio_lmb_count} tersedia)",
            callback_data=f"get_numbers:{count}:bio_lmb",
        )],
        [InlineKeyboardButton(
            text=f"🔵  LMB Only ({lmb_count} tersedia)",
            callback_data=f"get_numbers:{count}:lmb",
        )],
        [InlineKeyboardButton(
            text=f"📦  Semua Terdaftar ({total} tersedia)",
            callback_data=f"get_numbers:{count}:all",
        )],
        [InlineKeyboardButton(text="❌  Batal", callback_data="cancel_pick")],
    ])
    await callback.message.answer(
        f"<b>🎯 Filter Kualitas — {count} Nomor</b>\n"
        f"{SEP}\n"
        f"Pilih kualitas nomor yang mau diambil:",
        parse_mode="HTML",
        reply_markup=filter_kb,
    )


@router.callback_query(F.data.startswith("get_numbers:"))
async def cb_get_numbers(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()

    parts = callback.data.split(":")
    try:
        count = int(parts[1])
        filter_quality = parts[2]
    except (IndexError, ValueError):
        count = 5
        filter_quality = "all"

    numbers = database.get_random_numbers(count, filter_quality=filter_quality)

    if not numbers:
        await callback.message.answer(
            f"<b>📭 Stok Kosong!</b>\n"
            f"{SEP}\n"
            f"Tidak ada nomor yang sesuai filter.\n"
            f"Upload dulu via <b>📤 Upload Nomor</b>.",
            parse_mode="HTML",
            reply_markup=main_menu_keyboard(),
        )
        return

    lines = []
    for i, (num, q) in enumerate(numbers, 1):
        lines.append(f"{i}.  <code>{num}</code>  <i>[{quality_label_short(q)}]</i>")

    await callback.message.answer(
        f"<b>📱 {len(numbers)} Nomor Siap Didaftarkan</b>\n"
        f"{SEP}\n"
        f"{chr(10).join(lines)}\n"
        f"{SEP}\n"
        f"<i>Tap nomor untuk copy. OTP masuk langsung gue kabarin! ⚡</i>",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "cancel_pick")
async def cb_cancel_pick(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()
    await callback.message.answer(
        "❌ Dibatalin.",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "status")
async def cb_status(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()
    await _show_status(callback)


@router.callback_query(F.data == "clear_numbers")
async def cb_clear_numbers(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()
    confirm_kb = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(text="✅ Ya, Hapus Semua", callback_data="confirm_clear"),
            InlineKeyboardButton(text="❌ Batal", callback_data="cancel_clear"),
        ]
    ])
    await callback.message.answer(
        f"<b>⚠️ Hapus Semua Nomor?</b>\n"
        f"{SEP}\n"
        f"Semua nomor di stok akan dihapus permanen.\n"
        f"<b>Tidak bisa di-undo!</b>",
        parse_mode="HTML",
        reply_markup=confirm_kb,
    )


@router.callback_query(F.data == "confirm_clear")
async def cb_confirm_clear(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()
    deleted = database.clear_numbers()
    await callback.message.answer(
        f"<b>🗑 Stok Dibersihkan!</b>\n"
        f"{SEP}\n"
        f"<b>{deleted} nomor</b> berhasil dihapus.",
        parse_mode="HTML",
        reply_markup=main_menu_keyboard(),
    )


@router.callback_query(F.data == "cancel_clear")
async def cb_cancel_clear(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        await deny(callback)
        return
    await callback.answer()
    await callback.message.answer(
        "✅ Aman, nomor tidak jadi dihapus.",
        reply_markup=main_menu_keyboard(),
    )

# ─── iVAS SMS Poller ──────────────────────────────────────────────────────────

async def poll_ivasms(bot: Bot):
    logger.info(f"OTP poller started (default interval: {POLL_INTERVAL_DEFAULT}s)")

    if not ADMIN_CHAT_ID:
        logger.warning("ADMIN_CHAT_ID tidak diset — OTP polling dinonaktifkan")
        return

    client: IVASMSClient | None = None
    session_cookies: str = ""
    need_login: bool = True
    cookies_expired_notified: bool = False

    async def _close_client():
        nonlocal client, session_cookies, need_login
        if client:
            await client.close()
            client = None
        session_cookies = ""
        need_login = True

    while True:
        try:
            if is_poll_paused():
                await _close_client()
                try:
                    await asyncio.wait_for(_poll_event.wait(), timeout=5)
                    _poll_event.clear()
                except asyncio.TimeoutError:
                    pass
                continue

            current_cookies = get_ivasms_cookies()
            if not current_cookies:
                logger.warning("Cookies iVAS belum diset — polling skip")
                await _close_client()
                try:
                    await asyncio.wait_for(_poll_event.wait(), timeout=get_poll_interval())
                    _poll_event.clear()
                except asyncio.TimeoutError:
                    pass
                continue

            if current_cookies != session_cookies:
                await _close_client()
                client = IVASMSClient(current_cookies)
                await client.open()
                session_cookies = current_cookies
                need_login = True
                logger.info("Session baru dibuat dengan cookies terbaru")

            if need_login:
                logged_in = await client.login()
                if not logged_in:
                    logger.error("iVAS login gagal — cookies expired?")
                    if not cookies_expired_notified:
                        cookies_expired_notified = True
                        await bot.send_message(
                            ADMIN_CHAT_ID,
                            f"<b>🔴 Cookies iVAS Expired!</b>\n"
                            f"{SEP}\n"
                            f"Bot tidak bisa login ke iVAS SMS.\n\n"
                            f"<b>Cara fix:</b>\n"
                            f"1. Buka Chrome, login ke ivasms.com\n"
                            f"2. Ketik: <code>javascript:void(document.cookie)</code>\n"
                            f"3. Copy hasilnya, kirim:\n"
                            f"<code>/setcookies &lt;hasil_copy&gt;</code>",
                            parse_mode="HTML",
                        )
                    await _close_client()
                    try:
                        await asyncio.wait_for(_poll_event.wait(), timeout=get_poll_interval() * 5)
                        _poll_event.clear()
                    except asyncio.TimeoutError:
                        pass
                    continue

                cookies_expired_notified = False
                need_login = False
                logger.info("Login OK — session aktif, polling dimulai")

            today = date.today().strftime("%d/%m/%Y")
            messages = await client.get_all_otp_messages(from_date=today, to_date=today)

            updated_cookies = client.get_updated_cookies_str()
            if updated_cookies and updated_cookies != session_cookies:
                database.set_setting("ivasms_cookies", updated_cookies)
                session_cookies = updated_cookies
                logger.info("Cookies auto-diperbarui dari session iVAS")

            new_count = 0
            for msg in messages:
                phone = msg.get("phone_number", "")
                otp = msg.get("otp_message", "")
                if not otp:
                    continue
                if database.is_otp_seen(phone, otp):
                    continue
                database.mark_otp_seen(phone, otp)
                new_count += 1
                await bot.send_message(
                    ADMIN_CHAT_ID,
                    f"🔔 <b>OTP MASUK!</b>  ⚡\n"
                    f"{SEP}\n"
                    f"📱 <b>Nomor</b>\n"
                    f"<code>{phone}</code>\n\n"
                    f"🔑 <b>OTP</b>\n"
                    f"<code>{otp}</code>\n"
                    f"{SEP}\n"
                    f"<i>Tap OTP di atas untuk copy ✅</i>",
                    parse_mode="HTML",
                )

            if new_count:
                logger.info(f"Forwarded {new_count} new OTP(s)")

        except Exception as e:
            logger.error(f"Polling error: {e}")
            need_login = True

        interval = get_poll_interval()
        try:
            await asyncio.wait_for(_poll_event.wait(), timeout=interval)
            _poll_event.clear()
        except asyncio.TimeoutError:
            pass

# ─── Main ─────────────────────────────────────────────────────────────────────

async def main():
    if not BOT_TOKEN:
        raise ValueError(
            "BOT_TOKEN belum diset!\n"
            "Isi di config.json atau env var BOT_TOKEN."
        )

    database.init_db()

    bot = Bot(token=BOT_TOKEN)
    dp = Dispatcher()
    dp.include_router(router)

    railway_domain = os.getenv("RAILWAY_PUBLIC_DOMAIN", "").strip()

    if railway_domain:
        from aiohttp import web
        from aiogram.webhook.aiohttp_server import SimpleRequestHandler, setup_application

        webhook_path = "/webhook"
        webhook_url = f"https://{railway_domain}{webhook_path}"
        webhook_secret = os.getenv("WEBHOOK_SECRET", "ivasbot_secret_2026")

        await bot.delete_webhook(drop_pending_updates=True)
        await bot.set_webhook(webhook_url, secret_token=webhook_secret, drop_pending_updates=True)
        logger.info(f"Webhook aktif: {webhook_url}")

        app = web.Application()
        SimpleRequestHandler(dispatcher=dp, bot=bot, secret_token=webhook_secret).register(app, path=webhook_path)
        setup_application(app, dp, bot=bot)

        poller_task = asyncio.create_task(poll_ivasms(bot))

        port = int(os.getenv("PORT", 8080))
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "0.0.0.0", port)
        await site.start()
        logger.info(f"Webhook server listening on port {port}")

        try:
            await asyncio.Event().wait()
        finally:
            poller_task.cancel()
            await runner.cleanup()
            await bot.delete_webhook()
            await bot.session.close()

    else:
        logger.info("RAILWAY_PUBLIC_DOMAIN tidak ada — pakai polling mode (local dev)")
        poller_task = asyncio.create_task(poll_ivasms(bot))
        try:
            await dp.start_polling(bot, skip_updates=True)
        finally:
            poller_task.cancel()
            await bot.session.close()


if __name__ == "__main__":
    asyncio.run(main())
