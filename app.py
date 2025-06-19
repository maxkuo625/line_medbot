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
    PostbackAction,
    FlexSendMessage, BubbleContainer, BoxComponent, TextComponent, ButtonComponent, SeparatorComponent
)
from urllib.parse import quote, parse_qs # Import parse_qs
from handlers.message_handler import handle_text_message
from medication_reminder import handle_postback, create_patient_selection_message, get_patient_id_by_member_name, create_medication_management_menu, create_patient_edit_message # Import new functions
from scheduler import start_scheduler
from models import (
    generate_invite_code, bind_family,
    create_user_if_not_exists, get_family_members,
    set_temp_state, clear_temp_state, get_temp_state,
    get_medicine_id_by_name, add_medication_reminder_full
)
from database import get_conn # Assuming get_conn is in database.py
import json
import traceback # For better error logging

# 導入 OCR 解析模組
from medication_ocr_parser import call_ocr_service, parse_medication_order, convert_frequency_to_times

app = Flask(__name__)
line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

# Helper to reply messages
def reply_message(reply_token, messages):
    try:
        if not isinstance(messages, list):
            messages = [messages]
        line_bot_api.reply_message(reply_token, messages)
    except LineBotApiError as e:
        app.logger.error(f"LINE Bot API Error: {e.status_code} {e.error.message}")
        app.logger.error(f"Details: {e.error.details}")
        traceback.print_exc()

# ------------------------------------------------------------
# Flex Message - 主用藥管理選單
# ------------------------------------------------------------
def create_main_medication_menu():
    bubble = BubbleContainer(
        direction='ltr',
        hero=BoxComponent(
            layout='vertical',
            contents=[
                TextComponent(text='用藥提醒小幫手', weight='bold', size='xl', align='center'),
                TextComponent(text='請選擇功能：', size='sm', color='#666666', margin='md', align='center')
            ],
            padding_top='20px',
            padding_bottom='10px'
        ),
        body=BoxComponent(
            layout='vertical',
            contents=[
                ButtonComponent(
                    style='link',
                    height='sm',
                    action=MessageAction(label="使用說明", text="使用說明")
                ),
                SeparatorComponent(margin='md'),
                ButtonComponent( # 依照流程圖，將「選擇頻率」作為新增提醒的入口
                    style='link',
                    height='sm',
                    action=MessageAction(label="選擇頻率 (新增提醒)", text="選擇頻率")
                ),
                ButtonComponent(
                    style='link',
                    height='sm',
                    action=MessageAction(label="查詢用藥時間", text="查詢用藥時間")
                ),
                ButtonComponent(
                    style='link',
                    height='sm',
                    action=MessageAction(label="修改用藥時間", text="修改時間")
                ),
                SeparatorComponent(margin='md'),
                ButtonComponent(
                    style='link',
                    height='sm',
                    action=MessageAction(label="用藥管理 (刪除/新增藥品)", text="用藥管理")
                ),
                ButtonComponent(
                    style='link',
                    height='sm',
                    action=MessageAction(label="藥袋辨識", text="藥袋辨識")
                )
            ],
            padding_all='20px',
            spacing='md'
        )
    )
    return FlexSendMessage(alt_text="用藥提醒主選單", contents=bubble)

@app.route("/callback", methods=['POST'])
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
        abort(400)
    except LineBotApiError as e:
        app.logger.error(f"LINE Bot API Error: {e.status_code} {e.error.message}")
        app.logger.error(f"Details: {e.error.details}")
        abort(500)
    except Exception as e:
        app.logger.error(f"Webhook processing error: {e}")
        traceback.print_exc()
        abort(500)

    return 'OK'


@handler.add(MessageEvent, message=TextMessage)
def handle_message(event):
    reply_token = event.reply_token
    line_user_id = event.source.user_id
    message_text = event.message.text.strip()
    current_state_info = get_temp_state(line_user_id) or {}
    state = current_state_info.get("state")

    if message_text == "提醒用藥主選單":
        flex_message = create_main_medication_menu()
        line_bot_api.reply_message(event.reply_token, flex_message)
    elif message_text == "用藥管理":
        reply_message(reply_token, create_medication_management_menu(line_user_id))
    elif message_text == "新增用藥提醒":
        set_temp_state(line_user_id, {"state": "AWAITING_PATIENT_FOR_REMINDER"})
        reply_message(reply_token, create_patient_selection_message(line_user_id))
     # ✅ 使用者選擇手動輸入藥品
    elif message_text == "手動輸入藥品":
        set_temp_state(line_user_id, {"state": "AWAITING_MEDICINE_NAME", "member": current_state_info.get("member")})
        reply_message(reply_token, TextSendMessage(text="請輸入藥品名稱："))

    # ✅ 使用者輸入藥品名稱
    elif state == "AWAITING_MEDICINE_NAME":
        medicine_name = message_text
        set_temp_state(line_user_id, {
            "state": "AWAITING_FREQUENCY_SELECTION",
            "member": current_state_info.get("member"),
            "medicine_name": medicine_name
    })
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text=f"已輸入藥品：{medicine_name}\n請選擇用藥頻率：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=PostbackAction(label="每日一次", data="action=set_frequency_val&val=1_day")),
                QuickReplyButton(action=PostbackAction(label="每日二次", data="action=set_frequency_val&val=2_day")),
                QuickReplyButton(action=PostbackAction(label="每日三次", data="action=set_frequency_val&val=3_day")),
                QuickReplyButton(action=PostbackAction(label="需要時", data="action=set_frequency_val&val=as_needed"))
        ])
    ))
        
    # ✅ 使用者輸入用藥天數
    elif state == "AWAITING_DAYS_INPUT":
        days_text = message_text.strip()
        days = int(''.join(filter(str.isdigit, days_text))) if any(c.isdigit() for c in days_text) else 1
        current_state_info["days"] = days
        current_state_info["state"] = "AWAITING_TIME_SELECTION" 
        set_temp_state(line_user_id, current_state_info)
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text=f"✅ 已設定為使用 {days} 天。請選擇每天服藥時間（或多個時間）～"
        ))

    # ✅ 接收用藥時間（可多次）直到使用者輸入 "完成"
    elif state == "AWAITING_TIME_SELECTION":
        times = current_state_info.get("times", [])
        if message_text in ["完成", "ok", "OK"]:
            if not times:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="尚未輸入任何時間，請至少輸入一個時間（例如 08:00）"))
            else:
                from models import add_medication_reminder_full
                add_medication_reminder_full(
                    line_user_id,
                    current_state_info.get("member"),
                    current_state_info.get("medicine_name"),
                    current_state_info.get("frequency_code"),
                    current_state_info.get("dosage"),
                    current_state_info.get("days"),
                    times
                )
                clear_temp_state(line_user_id)
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"✅ 提醒已建立成功：\n藥品：{current_state_info.get('medicine_name')}\n時間：{', '.join(times)}"
                ))
        else:
            import re
            if re.match(r"^\d{1,2}:\d{2}$", message_text):
                times.append(message_text)
                current_state_info["times"] = times
                set_temp_state(line_user_id, current_state_info)
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"已加入時間：{message_text}。如需結束請輸入『完成』。"
                ))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text="請輸入正確的時間格式（例如 08:00）或輸入『完成』來結束"
                ))

    # ✅ 補上 confirm_dosage_correct 動作也會切到 AWAITING_DAYS_INPUT
    elif state == "AWAITING_DOSAGE_CONFIRM" and message_text in ["正確", "確定", "ok"]:
        set_temp_state(line_user_id, {"state": "AWAITING_DAYS_INPUT", **current_state_info})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入用藥天數，例如：7天、14天…"))

    elif state == "AWAITING_NEW_PATIENT_NAME":
        new_name = message_text
        clear_temp_state(line_user_id)
        conn = get_conn()
        reply_text = "抱歉，資料庫連線失敗。"
        if conn:
            try:
                cursor = conn.cursor()
                cursor.execute("SELECT recorder_id FROM users WHERE recorder_id = %s", (line_user_id,))
                user = cursor.fetchone()
                if user:
                    recorder_id_for_db = user[0]
                    cursor.execute("SELECT COUNT(*) FROM patients WHERE recorder_id = %s AND member = %s", (recorder_id_for_db, new_name))
                    if cursor.fetchone()[0] > 0: # 檢查計數是否大於 0
                        reply_text = f"成員「{new_name}」已經存在囉！"
                    else:
                        cursor.execute("INSERT INTO patients (recorder_id, member) VALUES (%s, %s)", (recorder_id_for_db, new_name))
                        conn.commit()
                        reply_text = f"好的，「{new_name}」已成功新增！"
                else:
                    reply_text = "抱歉，找不到您的使用者資料。"
            except Exception as e:
                app.logger.error(f"Error adding new patient: {e}")
                traceback.print_exc()
                reply_text = "新增成員失敗，請稍後再試。"
            finally:
                if conn.is_connected():
                    conn.close()
        reply_message(reply_token, TextSendMessage(text=reply_text))
    elif state == "AWAITING_NEW_NAME":
        new_name = message_text
        member_to_edit = current_state_info.get("member_to_edit")
        clear_temp_state(line_user_id)
        conn = get_conn()
        reply_text = "抱歉，資料庫連線失敗。"
        if conn and member_to_edit:
            try:
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE patients SET member = %s WHERE recorder_id = %s AND member = %s",
                    (new_name, line_user_id, member_to_edit)
                )
                conn.commit()
                if cursor.rowcount > 0:
                    reply_text = f"名稱已成功修改為「{new_name}」！"
                else:
                    reply_text = "修改失敗，找不到該成員。" # 或成員名稱重複導致更新失敗
            except Exception as e:
                app.logger.error(f"Error editing patient name: {e}")
                traceback.print_exc()
                reply_text = "修改名稱失敗，請稍後再試。"
            finally:
                if conn.is_connected():
                    conn.close()
        reply_message(reply_token, TextSendMessage(text=reply_text))
    else:
        handle_text_message(event, line_bot_api)


@handler.add(PostbackEvent)
def handle_postback_event(event):
    reply_token = event.reply_token
    line_user_id = event.source.user_id
    params = {k: v[0] for k, v in parse_qs(event.postback.data).items()}
    action = params.get("action")
    current_state_info = get_temp_state(line_user_id) or {}
    state = current_state_info.get("state")

    if action == "show_medication_management_menu":
        reply_message(reply_token, create_medication_management_menu(line_user_id))
    elif action == "select_patient_for_reminder":
        handle_postback(event, line_bot_api, {})
    elif action == "add_new_patient":
        set_temp_state(line_user_id, {"state": "AWAITING_NEW_PATIENT_NAME"})
        reply_message(reply_token, TextSendMessage(text="好的，請輸入您想新增的家人名稱："))
    elif action == "edit_patient_start":
        member_to_edit = params.get("member_to_edit")
        if member_to_edit:
            set_temp_state(line_user_id, {"state": "AWAITING_NEW_NAME", "member_to_edit": member_to_edit})
            reply_message(reply_token, TextSendMessage(text="好的，請輸入新的名稱："))
    elif action == "show_patient_edit_menu":
        reply_message(reply_token, create_patient_edit_message(line_user_id))
    else:
        handle_postback(event, line_bot_api, {}) # 傳遞空字典或依據 handle_postback 的實際定義移除此參數


# Start scheduler (assuming this is for background tasks)
start_scheduler(line_bot_api)

if __name__ == "__main__":
    app.run()