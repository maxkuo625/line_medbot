from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from config import CHANNEL_ACCESS_TOKEN, CHANNEL_SECRET
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage,
    PostbackEvent, FollowEvent, JoinEvent,
    QuickReply, QuickReplyButton,
    MessageAction, URIAction,
    DatetimePickerAction,
    PostbackAction
)
from urllib.parse import quote
from handlers.message_handler import handle_text_message
from medication_reminder import handle_medication_command, handle_postback
from medication_reminder import add_medication_reminder
from scheduler import start_scheduler
from models import (
    generate_invite_code, bind_family, get_user_by_line_id,
    create_user_if_not_exists, get_family_members,
    set_temp_state, clear_temp_state, get_temp_state,
    get_medicine_id_by_name
)

# 導入 OCR 解析模組
from medication_ocr_parser import call_ocr_service, parse_medication_order, convert_frequency_to_times

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

@handler.add(FollowEvent)
def handle_follow(event):
    """
    處理使用者首次加入好友事件。
    """
    user_id = event.source.user_id
    print(f"✅ 使用者 {user_id} 加入好友")
    create_user_if_not_exists(user_id)
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="歡迎加入！\n\n請輸入「綁定邀請碼」來綁定家庭帳號\n或輸入「產生邀請碼」建立您的家庭。")
    )

@handler.add(JoinEvent)
def handle_join(event):
    """
    處理 Bot 被邀請加入群組事件。
    """
    line_bot_api.reply_message(
        event.reply_token,
        TextSendMessage(text="感謝邀請我加入群組！我是一個用藥提醒機器人。")
    )

@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    """
    處理所有文字訊息。
    """
    user_id = event.source.user_id
    text = event.message.text.strip()
    print(f"收到來自 {user_id} 的訊息: {text}")

    # 委託給 medication_reminder 模組處理用藥提醒相關指令 (文字指令)
    if handle_medication_command(event, line_bot_api):
        return

    # --- 藥袋辨識相關的文字指令處理 ---
    if text == "藥袋辨識" or text == "上傳藥單": # 支援兩種觸發方式，符合流程圖和現有程式碼
        set_temp_state(user_id, {'state': 'awaiting_med_order_image'})
        # 流程圖中的提示訊息
        message = (
            "請拍攝藥袋照片。\n\n"
            "為了幫助我們準確辨識藥品，請依照以下建議拍攝：\n"
            "1. 請確保照片清晰且對焦正確\n"
            "2. 避免遮擋藥袋上的任何資訊\n"
            "3. 建議將藥袋平放拍攝，避免傾斜以利辨識"
        )
        # 流程圖中包含「開啟相機」和「打開相簿」的按鈕，
        # 這裡由於 LINE Bot 圖片上傳本身就支援這兩種方式，不需要額外按鈕。
        # 實際應用中，可以考慮使用 Flex Message 呈現更美觀的提示和說明。
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=message))
        return
    elif text == "取消藥單設定":
        clear_temp_state(user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="已取消藥單設定流程。"))
        return

    # 委託給通用的 message_handler 處理其他訊息
    handle_text_message(event, line_bot_api)


@handler.add(MessageEvent, message=ImageMessage)
def handle_image_message(event):
    """
    處理所有圖片訊息，特別是藥袋辨識流程中的圖片上傳。
    """
    user_id = event.source.user_id
    temp_state = get_temp_state(user_id)
    print(f"DEBUG: 收到來自 {user_id} 的圖片。目前暫存狀態: {temp_state}")

    # 檢查使用者是否處於等待藥袋圖片的狀態
    if temp_state and temp_state.get('state') == 'awaiting_med_order_image':
        try:
            # 獲取圖片的二進制數據
            message_content = line_bot_api.get_message_content(event.message.id)
            image_data = message_content.content # 獲取圖片的二進制數據

            # 1. 調用 OCR 服務
            # 這裡會調用 medication_ocr_parser 中的模擬函式，實際應替換為真實的 OCR API 調用
            ocr_raw_text = call_ocr_service(image_data)

            if not ocr_raw_text or ocr_raw_text.strip() == "":
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 無法從圖片中辨識出文字，請嘗試更清晰的圖片或手動設定。"))
                clear_temp_state(user_id)
                return

            # 2. 解析 OCR 辨識結果
            parsed_meds = parse_medication_order(ocr_raw_text) # 調用 medication_ocr_parser 中的解析函式

            if not parsed_meds:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❌ 成功辨識文字，但無法解析藥品資訊。請檢查藥單格式或手動設定。"))
                clear_temp_state(user_id)
                return

            # 3. 將解析結果暫存，並引導用戶確認
            temp_state['parsed_medications'] = parsed_meds
            temp_state['state'] = 'confirming_med_order' # 設定新狀態來處理確認流程
            set_temp_state(user_id, temp_state)

            # 構建確認訊息
            confirm_message_lines = ["✅ 辨識結果如下："]
            # 流程圖中提到看診日期和發藥天數，可以在這裡顯示
            # 這裡從 OCR 模擬結果中直接提取，如果你想從 parse_medication_order 返回更詳細的資訊，可以在那裡修改
            # 為了簡潔，這裡只顯示藥品列表
            
            confirm_message_lines.append("\n藥品名稱 | 單次劑量 | 用藥頻率 | 主要用途 | 副作用")
            confirm_message_lines.append("---|---|---|---|---")

            for med in parsed_meds:
                # 為了更好的顯示，處理可能為空的副作用
                side_effects_display = med['side_effects'] if med['side_effects'] else "無"
                confirm_message_lines.append(
                    f"{med['name']} | {med['dosage']} | {med['frequency_text']} | {med['purpose']} | {side_effects_display}"
                )
            
            confirm_message_lines.append("\n請確認資訊是否正確？")

            line_bot_api.reply_message(
                event.reply_token,
                TextSendMessage(
                    text="\n".join(confirm_message_lines),
                    quick_reply=QuickReply(items=[
                        QuickReplyButton(action=PostbackAction(label="✅ 是，存入藥歷", data="action=confirm_med_order")),
                        QuickReplyButton(action=MessageAction(label="❌ 否，重新辨識", text="取消藥單設定")) # 重新辨識可以引導回取消設定
                    ])
                )
            )
        except Exception as e:
            print(f"Error processing image message: {e}")
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="處理圖片時發生錯誤，請稍後再試。"))
            clear_temp_state(user_id)
    else:
        # 如果不是在等待藥單圖片的狀態，則提示用戶
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="如果您要設定藥單提醒，請先輸入「藥袋辨識」或「上傳藥單」。"))

@handler.add(PostbackEvent)
def handle_postback_event(event):
    """
    處理所有 Postback 事件。
    """
    data = event.postback.data
    user_id = event.source.user_id
    temp_state = get_temp_state(user_id) # 重新獲取狀態以防在 handle_medication_postback 之前被修改

    # 委託給 medication_reminder 模組的 handle_postback 處理所有與用藥提醒相關的 Postback
    if handle_postback(event, line_bot_api):
        return
    
    # --- 藥單確認流程的 Postback 處理 ---
    if data == "action=confirm_med_order" and temp_state and temp_state.get('state') == 'confirming_med_order':
        parsed_medications = temp_state.get('parsed_medications', [])
        
        if not parsed_medications:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="⚠️ 沒有可設定的藥品資訊。請重新上傳藥單。"))
            clear_temp_state(user_id)
            return

        success_count = 0
        fail_count = 0
        details = []

        for med in parsed_medications:
            med_name = med['name']
            dosage = med['dosage']
            frequency_text = med['frequency_text'] # 使用原始頻率描述作為頻率字段
            times_to_set = med['times'] # 這是已經轉換為 HH:MM 的時間列表
            
            # 對於「需要時服用」這種情況，可以特別處理，不設定具體提醒時間
            if "需要時服用" in frequency_text or "需要時" in frequency_text:
                details.append(f"❗ 藥品「{med_name}」為「{frequency_text}」，未設定固定提醒時間。")
                continue # 跳過此藥品的具體時間設定

            # 如果沒有解析到具體時間，給予提示
            if not times_to_set:
                details.append(f"❗ 藥品「{med_name}」的時段「{frequency_text}」無法解析為具體時間，請手動設定。")
                fail_count += 1
                continue

            # 針對每個具體時間設定提醒
            for single_time in set(times_to_set): # 使用 set 避免重複時間
                try:
                    # 假設 get_medicine_id_by_name 能夠根據藥品名稱獲取一個系統內的 ID
                    # 如果你的系統沒有預設藥品 ID，你需要調整這裡的邏輯，
                    # 例如直接使用藥品名稱，或者在 add_medication_reminder 內部處理新藥品。
                    medicine_id = get_medicine_id_by_name(med_name) # 假設這個函式能找到藥品ID

                    if medicine_id:
                        add_medication_reminder(user_id, medicine_id, single_time, dosage, frequency_text)
                        details.append(f"✅ {med_name} 在 {single_time} 設定成功。")
                        success_count += 1
                    else:
                        details.append(f"❌ 無法為藥品「{med_name}」找到對應的 ID，請確保藥品已存在於系統中，或手動設定。")
                        fail_count += 1
                except Exception as e:
                    details.append(f"❌ 設定 {med_name} 在 {single_time} 時失敗: {e}")
                    fail_count += 1
        
        final_message = f"藥單提醒設定完成！\n成功：{success_count} 個\n失敗：{fail_count} 個\n\n詳細：\n" + "\n".join(details)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=final_message))
        clear_temp_state(user_id) # 清除狀態，結束流程
        return

    # 處理來自 message_handler 的「分享給家人」Flex Message 觸發的 Postback
    if data.startswith("action=share_invite"):
        params = dict(item.split("=") for item in data.split("&")[1:])
        invite_code = params.get('code', '')
        
        invite_link = f"https://line.me/R/ti/p/@651omrog?code={invite_code}"
        share_text = (
            f"【邀請連結】\n\n"
            f"請點擊以下連結加入：\n"
            f"{invite_link}\n\n"
            f"邀請碼：{invite_code}"
        )
        
        encoded_share_text = quote(share_text)
        
        share_message_reply = TextSendMessage(
            text=share_text,
            quick_reply=QuickReply(items=[
                QuickReplyButton(
                    action=MessageAction(label="產生邀請連結", text=invite_code)
                ),
                QuickReplyButton(
                    action=URIAction(label="分享給朋友", uri=f"line://msg/text/?{encoded_share_text}")
                )
            ])
        )
        line_bot_api.reply_message(event.reply_token, share_message_reply)
        return

@app.route("/", methods=['POST'])
def callback():
    """
    LINE Bot 的 webhook 接收點。
    """
    signature = request.headers.get('X-Line-Signature')
    body = request.get_data(as_text=True)
    app.logger.info("Request body: " + body)

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid signature. Please check your channel access token/channel secret.")
        abort(400)
    except Exception as e:
        print(f"An error occurred: {e}")
        abort(500)

    return 'OK'

if __name__ == "__main__":
    # 確保你的 config.py 中有 CHANNEL_ACCESS_TOKEN 和 CHANNEL_SECRET
    # 並且 medication_reminder, scheduler, models 模組也已準備好
    start_scheduler(line_bot_api) # 啟動排程器，用於發送用藥提醒
    app.run(host='0.0.0.0', port=5000)

