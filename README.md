# Webhook Server Tích hợp SePay

Server này được thiết kế để xử lý webhooks thanh toán từ SePay và tự động xử lý đơn hàng bằng cách cập nhật cơ sở dữ liệu MySQL và gửi sản phẩm số (điện tử) thông qua Telegram Bot.

## Quy trình Thực thi (Execution Flow)

1. **Khởi tạo:** Server được xây dựng bằng Flask và bắt đầu lắng nghe trên cổng 5000 (`0.0.0.0:5000`). Nó nạp các cấu hình như `BOT_TOKEN`, `SEPAY_API_KEY`, và thông tin đăng nhập cơ sở dữ liệu từ file `.env`.
2. **Nhận Webhooks:** Khi khách hàng hoàn tất chuyển khoản ngân hàng, SePay sẽ gửi một HTTP POST request với chi tiết giao dịch đến endpoint `/sepay/webhook`.
3. **Xác thực:** Server xác thực webhook đến bằng cách kiểm tra header `Authorization` với khóa `SEPAY_API_KEY` đã được cấu hình.
4. **Xử lý Dữ liệu Webhook (Payload):**
   - Nội dung payload được lưu vào bảng `webhook_logs`.
   - Loại giao dịch (`transferType`) được kiểm tra để đảm bảo là tiền vào (`in`).
   - Server trích xuất mã giao dịch (`payment_code`) từ trường `code` hoặc bằng cách phân tích nội dung (`content`) của mô tả giao dịch.
5. **Tương tác Cơ sở dữ liệu:**
   - Server tìm đơn hàng đang chờ xử lý tương ứng trong bảng `don_hang` bằng `payment_code`.
   - Nó kiểm tra xem đơn hàng đã hết hạn chưa, đã được xử lý trước đó chưa, hoặc số tiền chuyển (`transferAmount`) đã đủ so với tổng tiền (`tong_tien`) cần thanh toán hay chưa.
   - Nếu mọi thứ hợp lệ, nó lấy các sản phẩm khả dụng (`sanpham`) khớp với danh mục của đơn hàng (`maloai`) theo số lượng yêu cầu (`so_luong`).
6. **Gửi sản phẩm qua Telegram:**
   - Nó đánh dấu các sản phẩm đã chọn là đã bán (`da_ban = 1`) và liên kết chúng với đơn hàng.
   - Trạng thái đơn hàng được cập nhật thành đã thanh toán (`paid`).
   - Chi tiết sản phẩm đã mua được định dạng và gửi đến người mua qua Telegram sử dụng module `python-telegram-bot` (dựa trên `telegram_chat_id` đã được liên kết với đơn hàng).

## Endpoints (Đường dẫn API)

### `POST /sepay/webhook`
- **Mục đích:** Nhận các sự kiện webhook từ dịch vụ SePay.
- **Headers Yêu cầu:** 
  - `Authorization: Apikey <SEPAY_API_KEY>` (Sẽ bị bỏ qua nếu `SEPAY_API_KEY` không được thiết lập).
- **Body Request:** Dữ liệu JSON định nghĩa giao dịch SePay (yêu cầu các trường như `transferType`, `transferAmount`, `id`, `code`, hoặc `content`).
- **Phản hồi (Responses):**
  - `401 Unauthorized`: API key không khớp.
  - `200 OK`: Xử lý thành công (hoặc bỏ qua một cách an toàn nếu giao dịch cung cấp dữ liệu không liên quan, ví dụ như `transferType != "in"`).
  - `500 Internal Server Error`: Dành cho các lỗi cơ sở dữ liệu hoặc các ngoại lệ không mong muốn khác.

## Hướng dẫn Cài đặt
1. Cài đặt các thư viện cần thiết:
   ```bash
   pip install -r requirements.txt
   ```
2. Tạo file `.env` trong thư mục gốc của dự án:
   ```env
   BOT_TOKEN=your_telegram_bot_token_cua_ban
   SEPAY_API_KEY=your_sepay_api_key_cua_ban
   DB_HOST=localhost
   DB_USER=root
   DB_PASSWORD=mat_khau_cua_ban
   DB_NAME=BOT_TELEGRAM_DB
   ```
3. Chạy ứng dụng:
   ```bash
   python webhook_server.py
   ```
