import asyncio
import cloudscraper
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton, ReplyKeyboardRemove

# ---------- Конфигурация ----------
RC_URL = "YOUR_ROCKET_CHAT_URL"
RC_LOGIN = "YOUR_ROCKET_CHAT_LOGIN"
RC_PASSWORD = "YOUR_ROCKET_CHAT_PASSWORD"
BOT_ROOM_ID = "YOUR_RC_ROOM_ID"

RC_USER_ID = None
RC_AUTH_TOKEN = None

scraper = cloudscraper.create_scraper()

# --- Telegram ---
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
TELEGRAM_CHAT_ID = "YOUR_TELEGRAM_CHAT_ID"

bot = Bot(token=TELEGRAM_TOKEN)
dp = Dispatcher()

# ---------- Состояния пользователей ----------
user_state = {}  # {user_id: "waiting_code" | "ready_price"}
waiting_rc_for_user = {} # {user_id: last_user_message_text}
sent_ids = set()  # _id сообщений RC, которые уже переслали

# ---------- Rocket.Chat ----------
def rc_login():
    global RC_USER_ID, RC_AUTH_TOKEN
    url = f"{RC_URL}/api/v1/login"
    r = scraper.post(url, json={"user": RC_LOGIN, "password": RC_PASSWORD})
    if r.status_code == 200 and "data" in r.json():
        data = r.json()["data"]
        RC_USER_ID = data["userId"]
        RC_AUTH_TOKEN = data["authToken"]
        print("✅ Логин в Rocket.Chat успешен")
    else:
        print("❌ Ошибка логина:", r.status_code, r.text[:200])
        raise SystemExit("Не удалось авторизоваться в Rocket.Chat")

def rc_headers():
    return {
        "X-User-Id": RC_USER_ID,
        "X-Auth-Token": RC_AUTH_TOKEN,
        "User-Agent": "Mozilla/5.0"
    }

def send_to_rocketchat(text: str):
    url = f"{RC_URL}/api/v1/chat.postMessage"
    r = scraper.post(url, headers=rc_headers(), json={"roomId": BOT_ROOM_ID, "text": text})
    if r.status_code != 200:
        print("Ошибка отправки в Rocket.Chat:", r.text[:200])

def get_new_rc_messages():
    url = f"{RC_URL}/api/v1/im.history?roomId={BOT_ROOM_ID}&count=50"
    r = scraper.get(url, headers=rc_headers())
    try:
        messages = r.json().get("messages", [])
        return [m for m in messages if m["u"]["_id"] != RC_USER_ID]
    except Exception:
        print("Ошибка ответа RC:", r.status_code, r.text[:200])
        return []

# ---------- Кнопка ----------
async def show_price_button(user_id):
    if user_state.get(user_id) == "ready_price":
        keyboard = ReplyKeyboardMarkup(
            keyboard=[[KeyboardButton(text="Перевірити Ціну")]],
            resize_keyboard=True,
            one_time_keyboard=True
        )
        await bot.send_message(
            chat_id=user_id,
            text="Натисни для перевірки ціни",
            reply_markup=keyboard
        )

# ---------- Telegram handlers ----------
@dp.message(Command("start"))
async def start(message: types.Message):
    user_id = message.from_user.id
    user_state[user_id] = "ready_price"
    await show_price_button(user_id)

@dp.message()
async def handle_message(message: types.Message):
    user_id = message.from_user.id
    if message.chat.id != TELEGRAM_CHAT_ID:
        return

    if message.text == "Перевірити Ціну":
        send_to_rocketchat("Перевірити Ціну")
        user_state[user_id] = "waiting_code"
    elif user_state.get(user_id) == "waiting_code":
        send_to_rocketchat(message.text)
        waiting_rc_for_user[user_id] = message.text  # ждём ответа RC на этот код
    elif user_state.get(user_id) == "ready_price":
        await message.answer("Спочатку натисніть кнопку 'Перевірити Ціну'")

# ---------- Фоновый таск: слушаем RC ----------
async def poll_rc():
    initial_messages = get_new_rc_messages()
    for m in initial_messages:
        sent_ids.add(m["_id"])

    rc_buffer = {}  # {user_id: list_of_messages}

    while True:
        new_msgs = get_new_rc_messages()
        new_msgs.sort(key=lambda x: x["_id"])
        new_msgs = [m for m in new_msgs if m["_id"] not in sent_ids]

        for msg in new_msgs:
            sent_ids.add(msg["_id"])
            text_to_send = msg.get("msg") or str(msg)

            # Если это сообщение RC с просьбой ввести код
            if "Вкажіть код товару або посилання на товар" in text_to_send:
                await bot.send_message(TELEGRAM_CHAT_ID, text_to_send)
                continue

            # Ищем пользователя, который ждал ответа на свой код
            for user_id, last_code in list(waiting_rc_for_user.items()):
                if last_code in text_to_send:
                    if user_id not in rc_buffer:
                        rc_buffer[user_id] = []
                    rc_buffer[user_id].append(text_to_send)

        # Отправляем объединённые сообщения пользователю и показываем кнопку
        for user_id, messages_list in list(rc_buffer.items()):
            if messages_list:
                combined_text = "\n\n".join(messages_list)
                await bot.send_message(user_id, combined_text)
                user_state[user_id] = "ready_price"
                await show_price_button(user_id)
                del waiting_rc_for_user[user_id]
                del rc_buffer[user_id]

        await asyncio.sleep(1)
# ---------- Запуск ----------
async def main():
    rc_login()
    asyncio.create_task(poll_rc())
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())