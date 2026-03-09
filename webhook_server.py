import os
import re
import json
import logging
from decimal import Decimal, InvalidOperation

import mysql.connector
from mysql.connector import Error
from dotenv import load_dotenv
from flask import Flask, request, jsonify
from telegram import Bot

load_dotenv()

app = Flask(__name__)

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
SEPAY_API_KEY = os.getenv("SEPAY_API_KEY", "")

DB_CONFIG = {
    "host": os.getenv("DB_HOST", "interchange.proxy.rlwy.net"),
    "port": int(os.getenv("DB_PORT", "42341")),
    "user": os.getenv("DB_USER", "root"),
    "password": os.getenv("DB_PASSWORD", ""),
    "database": os.getenv("DB_NAME", "railway"),
    "charset": "utf8mb4",
}

bot = Bot(token=BOT_TOKEN)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s"
)


def get_db_connection():
    return mysql.connector.connect(**DB_CONFIG)


def format_currency(v):
    try:
        amount = Decimal(str(v))
    except (InvalidOperation, TypeError, ValueError):
        amount = Decimal("0")
    return f"{int(amount):,}".replace(",", ".") + "đ"


def parse_amount(value):
    try:
        return Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0")


def extract_payment_code(payload):
    code = payload.get("code")
    content = payload.get("content", "") or ""

    if code:
        return str(code).strip().upper()

    match = re.search(r"(TT[A-Z0-9]{10})", content.upper())
    if match:
        return match.group(1)

    return None


def save_webhook_log(payload):
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO webhook_logs (provider, payload_json) VALUES (%s, %s)",
            ("sepay", json.dumps(payload, ensure_ascii=False))
        )
        conn.commit()
    except Exception as e:
        logging.exception("Lỗi ghi webhook log: %s", e)
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()


@app.route("/sepay/webhook", methods=["POST"])
def sepay_webhook():
    auth_header = request.headers.get("Authorization", "")
    expected = f"Apikey {SEPAY_API_KEY}"

    if SEPAY_API_KEY and auth_header != expected:
        return jsonify({"success": False, "message": "Unauthorized"}), 401

    payload = request.get_json(silent=True) or {}
    save_webhook_log(payload)

    transfer_type = payload.get("transferType")
    transfer_amount = parse_amount(payload.get("transferAmount"))
    sepay_transaction_id = payload.get("id")
    payment_code = extract_payment_code(payload)

    if transfer_type != "in":
        return jsonify({"success": True, "message": "Ignored"}), 200

    if not payment_code:
        return jsonify({"success": True, "message": "No payment code"}), 200

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        conn.start_transaction()

        cursor.execute(
            """
            SELECT *
            FROM don_hang
            WHERE payment_code = %s
            LIMIT 1
            FOR UPDATE
            """,
            (payment_code,)
        )
        order = cursor.fetchone()

        if not order:
            conn.rollback()
            return jsonify({"success": True, "message": "Order not found"}), 200

        if order["payment_status"] != "pending":
            conn.rollback()
            return jsonify({"success": True, "message": "Order is not pending"}), 200

        if order["expired_at"] is not None:
            cursor.execute(
                """
                UPDATE don_hang
                SET payment_status = 'expired'
                WHERE id = %s
                  AND payment_status = 'pending'
                  AND expired_at <= NOW()
                """,
                (order["id"],)
            )
            if cursor.rowcount > 0:
                conn.commit()
                return jsonify({"success": True, "message": "Order expired"}), 200

        cursor.execute(
            """
            SELECT *
            FROM don_hang
            WHERE id = %s
            LIMIT 1
            FOR UPDATE
            """,
            (order["id"],)
        )
        order = cursor.fetchone()

        if not order:
            conn.rollback()
            return jsonify({"success": False, "message": "Order disappeared"}), 500

        if order["payment_status"] != "pending":
            conn.rollback()
            return jsonify({"success": True, "message": "Order is not pending"}), 200

        tong_tien = parse_amount(order["tong_tien"])
        if transfer_amount < tong_tien:
            conn.rollback()
            return jsonify({"success": True, "message": "Insufficient amount"}), 200

        cursor.execute(
            """
            SELECT masp, tensanpham, noi_dung
            FROM sanpham
            WHERE maloai = %s AND da_ban = 0
            ORDER BY created_at, masp
            LIMIT %s
            FOR UPDATE
            """,
            (order["maloai"], order["so_luong"])
        )
        products = cursor.fetchall()

        if len(products) < order["so_luong"]:
            conn.rollback()
            return jsonify({"success": False, "message": "Not enough stock"}), 200

        cursor.execute(
            """
            UPDATE don_hang
            SET payment_status = 'paid',
                sepay_transaction_id = %s,
                paid_at = NOW()
            WHERE id = %s
            """,
            (str(sepay_transaction_id), order["id"])
        )

        delivered_lines = []
        for idx, p in enumerate(products, start=1):
            cursor.execute(
                """
                UPDATE sanpham
                SET da_ban = 1, sold_order_id = %s
                WHERE masp = %s
                """,
                (order["id"], p["masp"])
            )

            cursor.execute(
                """
                INSERT INTO don_hang_chi_tiet (order_id, masp)
                VALUES (%s, %s)
                """,
                (order["id"], p["masp"])
            )

            delivered_lines.append(
                f"{idx}. {p['tensanpham']}\n{p['noi_dung']}"
            )

        conn.commit()

    except Exception as e:
        if conn:
            conn.rollback()
        logging.exception("Webhook error: %s", e)
        return jsonify({"success": False, "message": "Internal server error"}), 500
    finally:
        if cursor:
            cursor.close()
        if conn and conn.is_connected():
            conn.close()

    try:
        text = (
            f"✅ THANH TOÁN THÀNH CÔNG\n\n"
            f"Mã đơn: {order['order_code']}\n"
            f"Sản phẩm: {order['tenloai']}\n"
            f"Số lượng: {order['so_luong']}\n"
            f"Số tiền: {format_currency(order['tong_tien'])}\n\n"
            f"📦 THÔNG TIN SẢN PHẨM:\n\n"
            + "\n\n".join(delivered_lines)
            + "\n\n🔒 Vui lòng lưu lại thông tin ngay."
        )

        bot.send_message(chat_id=order["telegram_chat_id"], text=text)
    except Exception as e:
        logging.exception("Lỗi gửi Telegram: %s", e)

    return jsonify({"success": True, "message": "Payment confirmed"}), 200


# if __name__ == "__main__":
#     if not BOT_TOKEN:
#         raise ValueError("Thiếu BOT_TOKEN")
#     if not SEPAY_API_KEY:
#         logging.warning("SEPAY_API_KEY đang trống. Webhook sẽ không kiểm tra Authorization.")

#     app.run(host="0.0.0.0", port=5000, debug=False)

if __name__ == "__main__":
    if not BOT_TOKEN:
        raise ValueError("Thiếu BOT_TOKEN")
    if not SEPAY_API_KEY:
        logging.warning("SEPAY_API_KEY đang trống. Webhook sẽ không kiểm tra Authorization.")

    port = int(os.getenv("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)