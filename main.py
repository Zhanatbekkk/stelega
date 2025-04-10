# Конечный код

import logging
import sqlite3
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import pytz
import os
from telegram import Update, ReplyKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

TOKEN = "7539763755:AAFcu3JvOUEY7ZkpCR3K4Z1m-ScPd8bNVfI"  # Замени на свой токен от BotFather

logging.basicConfig(level=logging.INFO)

tz = pytz.timezone("Asia/Almaty")
now = datetime.now(tz)

formatted = now.strftime("%d.%m.%Y %H:%M:%S")
print(formatted)

# --- Инициализация базы ---
def init_db():
    print("[INIT] Создание базы данных...")
    conn = sqlite3.connect("rates.db")
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS rates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            currency TEXT,
            date TEXT,
            rate REAL
        )
    """
    )
    conn.commit()
    conn.close()
    print("[INIT] База готова.")


# --- Получение курсов за одну дату ---
def get_rates_for_date(date: datetime):
    formatted_date = date.strftime("%d.%m.%Y")
    url = f"https://nationalbank.kz/rss/get_rates.cfm?fdate={formatted_date}"
    print(f"[HTTP] Запрос к {url}")
    response = requests.get(url)
    print(f"[HTTP] Код ответа: {response.status_code}")
    if response.status_code != 200:
        return None
    root = ET.fromstring(response.content)

    target = {
        "РОССИЙСКИЙ РУБЛЬ": "RUB",
        "КИТАЙСКИЙ ЮАНЬ": "CNY",
        "УЗБЕКСКИХ СУМОВ": "UZS",
        "ДОЛЛАР США": "USD",
    }

    result = []
    for item in root.findall("item"):
        fullname = item.find("fullname").text.upper()
        rate = float(item.find("description").text)
        if fullname in target:
            result.append((target[fullname], date.strftime("%Y-%m-%d"), rate))
    return result


# --- Обновляем курсы за 7 дней ---
def update_rates():
    print("[UPDATE] Обновление курсов за 7 дней...")
    today = datetime.now(pytz.timezone("Asia/Almaty"))
    conn = sqlite3.connect("rates.db")
    c = conn.cursor()
    today = datetime.now()
    for i in range(7):
        date = today - timedelta(days=i)
        c.execute("SELECT 1 FROM rates WHERE date = ?", (date.strftime("%Y-%m-%d"),))
        if c.fetchone():
            continue
        print(f"[LOAD] Загружаем курс за {date.strftime('%Y-%m-%d')}")
        rates = get_rates_for_date(date)
        if rates:
            c.executemany(
                "INSERT INTO rates (currency, date, rate) VALUES (?, ?, ?)", rates
            )
    conn.commit()
    conn.close()
    print("[UPDATE] Обновление завершено.")


# --- Текстовый ASCII-график ---
def build_text_chart(currency_code: str, currency_name: str):
    conn = sqlite3.connect("rates.db")
    c = conn.cursor()
    c.execute("SELECT date, rate FROM rates WHERE currency = ? ORDER BY date DESC LIMIT 7", (currency_code,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        return "Нет данных для построения графика."

    rows.reverse()
    min_rate = min(rate for _, rate in rows)
    step = 0.5  # каждая точка = 0.5 ₸
    max_dots = 10

    text = f"📉 Курс {currency_name} за 7 дней:\n\n"
    for date_str, rate in rows:
        date_fmt = datetime.strptime(date_str, "%Y-%m-%d").strftime("%d.%m")
        diff = rate - min_rate
        bar_len = min(int(diff / step), max_dots)
        dots = '•' * (bar_len or 1)
        text += f"{date_fmt} | {rate:>6.2f} ₸ | {dots}\n"

    return text




# --- Telegram UI ---
main_keyboard = ReplyKeyboardMarkup(
    [["\U0001f4b9 Курсы валют", "\U0001f4c9 График"], ["\U0001f4b8 Конвертация"]],
    resize_keyboard=True,
)
graph_keyboard = ReplyKeyboardMarkup(
    [["📉 USD", "📉 RUB", "📉 CNY", "📉 UZS"], ["⬅️ Назад в меню"]], resize_keyboard=True
)
convert_keyboard = ReplyKeyboardMarkup(
    [["USD", "RUB", "CNY", "UZS"], ["🏠 В меню"]], resize_keyboard=True
)

user_convert_state = {}


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Выберите действие:", reply_markup=main_keyboard)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text

    if text == "\U0001f4b9 Курсы валют":
        user_convert_state.pop(user_id, None)
        await show_rates(update)

    elif text == "\U0001f4c9 График":
        user_convert_state[user_id] = {"mode": "graph"}
        await update.message.reply_text("Выберите валюту:", reply_markup=graph_keyboard)

    elif text.startswith("📉"):
        if user_convert_state.get(user_id, {}).get("mode") == "graph":
            code = text.split()[1]
            names = {
                "USD": "доллара США",
                "RUB": "рубля",
                "CNY": "юаня",
                "UZS": "узбекского сума",
            }
            name = names.get(code, code)
            await send_graph(update, code, name)
        else:
            await update.message.reply_text("Нажмите 'График', чтобы выбрать валюту.")

    elif text == "💸 Конвертация":
        user_convert_state[user_id] = {"step": "choose_currency", "mode": "convert"}
        await update.message.reply_text(
            "Выберите валюту для конвертации:", reply_markup=convert_keyboard
        )

    elif text == "⬅️ Назад в меню" or text == "🏠 В меню":
        user_convert_state.pop(user_id, None)
        await update.message.reply_text(
            "Выберите действие:", reply_markup=main_keyboard
        )

    elif text in ["USD", "RUB", "CNY", "UZS"]:
        if user_convert_state.get(user_id, {}).get("mode") == "convert":
            user_convert_state[user_id]["currency"] = text
            user_convert_state[user_id]["step"] = "enter_amount"
            await update.message.reply_text(
                f"Введите сумму в {text}:", reply_markup=convert_keyboard
            )
        else:
            await update.message.reply_text("Нажмите 'Конвертация', чтобы начать.")

    elif user_convert_state.get(user_id, {}).get("step") == "enter_amount":
        try:
            amount = float(text.replace(",", "."))
            from_currency = user_convert_state[user_id]["currency"]
            to_currency = "KZT"
            conn = sqlite3.connect("rates.db")
            c = conn.cursor()
            today = datetime.now().strftime("%Y-%m-%d")
            c.execute(
                "SELECT rate FROM rates WHERE currency = ? AND date = ?",
                (from_currency, today),
            )
            from_rate = c.fetchone()
            conn.close()
            if from_rate:
                result = amount * from_rate[0]
                await update.message.reply_text(
                    f"{amount} {from_currency} = {round(result, 2)} ₸"
                )
            else:
                await update.message.reply_text("Курс не найден в базе данных.")
        except:
            await update.message.reply_text("Введите число. Например: 100")

    else:
        await update.message.reply_text("Пожалуйста, выбери кнопку на клавиатуре.")


async def show_rates(update: Update):
    tz = pytz.timezone("Asia/Almaty")
    now = datetime.now()
    date = now.strftime("%d.%m.%Y %H:%M:%S")
    conn = sqlite3.connect("rates.db")
    c = conn.cursor()
    c.execute(
        "SELECT currency, rate FROM rates WHERE date = ?", (now.strftime("%Y-%m-%d"),)
    )
    rows = c.fetchall()
    conn.close()

    code_to_label = {
        "USD": "🇺🇸 Доллар США",
        "RUB": "🇷🇺 Рубль",
        "CNY": "🇨🇳 Юань",
        "UZS": "🇺🇿 Узбекский сум",
    }

    text = f"💱 Курсы валют на {date}:\n\n"
    for code, rate in rows:
        label = code_to_label.get(code, code)
        text += f"{label}: {rate} ₸\n"

    await update.message.reply_text(text)


async def send_graph(update: Update, code, name):
    chart_text = build_text_chart(code, name)
    await update.message.reply_text(chart_text)


# --- Main ---
def main():
    print("[START] Запуск бота...")
    init_db()
    update_rates()
    app = ApplicationBuilder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    app.run_polling()
    print("[READY] Бот запущен и работает.")


if __name__ == "__main__":
    main()
