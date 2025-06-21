from flask import Flask, request, abort
from linebot import LineBotApi, WebhookHandler
from config import CHANNEL_ACCESS_TOKEN, CHANNEL_SECRET
from linebot.exceptions import InvalidSignatureError, LineBotApiError
from linebot.models import (
    MessageEvent, TextMessage, TextSendMessage, ImageMessage,
    PostbackEvent,QuickReply, QuickReplyButton,MessageAction, 
    URIAction,DatetimePickerAction,PostbackAction,FlexSendMessage, 
    BubbleContainer, BoxComponent, TextComponent, ButtonComponent, 
    SeparatorComponent,TemplateSendMessage, ButtonsTemplate, FollowEvent
)
from urllib.parse import parse_qs, quote
from handlers.message_handler import handle_text_message, handle_family_postback
from medication_reminder import (
    handle_postback, create_patient_selection_message, create_medication_management_menu, 
    create_patient_edit_message, create_frequency_quickreply)
from scheduler import start_scheduler
from models import (
    set_temp_state, clear_temp_state, get_temp_state, add_medication_reminder_full,
    get_times_per_day_by_code, get_frequency_name_by_code
)
from database import get_conn
import json
import traceback

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

def create_family_management_menu():
    contents = [
        ButtonComponent(
            style="link",
            height="sm",
            action=MessageAction(label="🔗 產生邀請碼", text="產生邀請碼")
        ),
        ButtonComponent(
            style="link",
            height="sm",
            action=MessageAction(label="📥 綁定家人", text="綁定")
        ),
        ButtonComponent(
            style="link",
            height="sm",
            action=MessageAction(label="🔍 查詢家人", text="查詢家人")
        ),
        ButtonComponent(
            style="link",
            height="sm",
            action=MessageAction(label="❌ 解除綁定", text="解除綁定")
        )
    ]

    bubble = BubbleContainer(
        direction="ltr",
        body=BoxComponent(
            layout="vertical",
            contents=[
                TextComponent(text="👨‍👩‍👧‍👦 家人管理選單", weight="bold", size="lg", align="center"),
                SeparatorComponent(margin="md"),
                *contents
            ],
            spacing="md",
            padding_all="20px"
        )
    )

    return FlexSendMessage(alt_text="家人管理選單", contents=bubble)

def welcome_invited_user(reply_token, line_bot_api):
    line_bot_api.reply_message(reply_token, [
        TextSendMessage(text="👋 歡迎加入！請點選下方『家人管理選單』完成綁定流程。"),
        create_family_management_menu()
    ])

@handler.add(FollowEvent)
def handle_follow(event):
    reply_token = event.reply_token
    welcome_invited_user(reply_token, line_bot_api)



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
    elif message_text == "家人管理":
        line_bot_api.reply_message(reply_token, create_family_management_menu())
    elif message_text == "用藥管理":
        reply_message(reply_token, create_medication_management_menu(line_user_id))
    elif message_text == "新增用藥提醒":
        set_temp_state(line_user_id, {"state": "AWAITING_PATIENT_FOR_REMINDER"})
        reply_message(reply_token, create_patient_selection_message(line_user_id))
    elif message_text == "查詢用藥時間": # 新增此判斷
        set_temp_state(line_user_id, {"state": "AWAITING_PATIENT_FOR_QUERY"}) # 設定新狀態
        reply_message(reply_token, create_patient_selection_message(line_user_id, context="query_reminder"))
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
            quick_reply=create_frequency_quickreply()
    ))
        
    # ✅ 新增處理劑量輸入的狀態
    elif state == "AWAITING_DOSAGE_INPUT":
        dosage = message_text.strip()
        if not dosage: # 簡單的驗證，避免空劑量
            line_bot_api.reply_message(reply_token, TextSendMessage(text="劑量不能為空，請重新輸入。"))
            return

        current_state_info["dosage"] = dosage
        current_state_info["state"] = "AWAITING_DAYS_INPUT" # 假設劑量後直接進入天數輸入
        set_temp_state(line_user_id, current_state_info)
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已輸入劑量：{dosage}。請輸入用藥天數，例如：7天、14天…"))
        
    # ✅ 使用者輸入用藥天數
    elif state == "AWAITING_DAYS_INPUT":
        days = message_text.strip()
        if not days.replace("天", "").isdigit():
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入有效的天數，例如 7天"))
            return

        current_state_info["days"] = days.replace("天", "")
        current_state_info["state"] = "AWAITING_TIME_SELECTION"
        current_state_info["times"] = []
        set_temp_state(line_user_id, current_state_info)

        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(
                text="請選擇第一個提醒時間：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(
                        action=DatetimePickerAction(
                            label="➕ 選擇時間",
                            data="action=set_time",
                            mode="time"
                        )
                    ),
                    QuickReplyButton(
                        action=PostbackAction(
                            label="✅ 完成",
                            data="action=finish_time_selection"
                        )
                    )
                ])
            )
        )


    # ✅ 接收用藥時間（可多次
    elif state == "AWAITING_TIME_SELECTION":
        selected_times = current_state_info.get("times", [])
        current_display = "、".join(selected_times) if selected_times else "無"

    # 快速回覆選項：依照剩餘次數顯示
        quick_items = []

        if len(selected_times) < 4:
            quick_items.append(
                QuickReplyButton(
                    action=DatetimePickerAction(
                        label="➕ 選擇時間",
                        data="action=set_time",
                        mode="time"
                    )
                )
            )

        quick_items.append(
            QuickReplyButton(
                action=PostbackAction(
                    label="✅ 完成",
                    data="action=finish_time_selection"
                )
            )
        )

        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(
                text=f"目前已選擇時間：{current_display}\n"
                     f"{'最多可設定 4 個時間。' if len(selected_times) < 4 else '已達上限，請按完成繼續。'}",
                quick_reply=QuickReply(items=quick_items)
            )
        )



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

    if action in ["confirm_unbind"]:
        handle_family_postback(event, line_bot_api)
    else:
        handle_postback(event, line_bot_api, {})

    # ✅ set_time 處理時間新增（根據 frequency_code 限制）
    if action == "set_time" and state == "AWAITING_TIME_SELECTION":
        selected_time = event.postback.params.get('time')
        times = current_state_info.get("times", [])
        frequency_code = current_state_info.get("frequency_code")
        max_times = get_times_per_day_by_code(frequency_code) or 4
        if max_times == 0:
            max_times = 1

        if selected_time in times:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"⏰ {selected_time} 已經選過了，請選其他時間。"))
            return

        if len(times) >= max_times:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text=f"⚠️ 此頻率（{frequency_code}）最多只能設定 {max_times} 個提醒時間。請按完成繼續。"
            ))
            return

        times.append(selected_time)
        current_state_info["times"] = times
        set_temp_state(line_user_id, current_state_info)

        selected_times = current_state_info.get("times", [])
        current_display = "、".join(selected_times) if selected_times else "無"

        quick_items = []
        if len(selected_times) < max_times:
            quick_items.append(QuickReplyButton(
                action=DatetimePickerAction(
                    label="➕ 選擇時間",
                    data="action=set_time",
                    mode="time"
                )
            ))

        for t in selected_times:
            quick_items.append(QuickReplyButton(
                action=PostbackAction(
                    label=f"🗑 刪除 {t}",
                    data=f"action=delete_selected_time&time={t}"
                )
            ))

        quick_items.append(QuickReplyButton(
            action=PostbackAction(
                label="✅ 完成",
                data="action=finish_time_selection"
            )
        ))

        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(
                text=f"✅ 已新增時間：{selected_time}\n目前已選擇：{current_display}\n（此頻率最多可設定 {max_times} 次提醒）",
                quick_reply=QuickReply(items=quick_items)
            )
        )

    
    # ✅ delete_selected_time 處理刪除後重建畫面（依頻率限制）
    elif action == "delete_selected_time" and state == "AWAITING_TIME_SELECTION":
        time_to_delete = params.get("time")
        times = current_state_info.get("times", [])
        frequency_code = current_state_info.get("frequency_code")
        max_times = get_times_per_day_by_code(frequency_code) or 4
        if max_times == 0:
            max_times = 1

        if time_to_delete in times:
            times.remove(time_to_delete)
            current_state_info["times"] = times
            set_temp_state(line_user_id, current_state_info)
            msg = f"🗑 已刪除時間：{time_to_delete}"
        else:
            msg = f"⚠️ 找不到時間：{time_to_delete}"

        selected_times = current_state_info.get("times", [])
        current_display = "、".join(selected_times) if selected_times else "無"

        quick_items = []
        if len(selected_times) < max_times:
            quick_items.append(QuickReplyButton(
                action=DatetimePickerAction(
                    label="➕ 選擇時間",
                    data="action=set_time",
                    mode="time"
                )
            ))

        for t in selected_times:
            quick_items.append(QuickReplyButton(
                action=PostbackAction(
                    label=f"🗑 刪除 {t}",
                    data=f"action=delete_selected_time&time={t}"
                )
            ))

        quick_items.append(QuickReplyButton(
            action=PostbackAction(
                label="✅ 完成",
                data="action=finish_time_selection"
            )
        ))

        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(
                text=f"{msg}\n\n目前已選擇時間：{current_display}\n（此頻率最多可設定 {max_times} 次提醒）",
                quick_reply=QuickReply(items=quick_items)
            )
        )

    # ✅ finish_time_selection - 顯示中文頻率名稱於結果中
    elif action == "finish_time_selection" and state == "AWAITING_TIME_SELECTION":
        times = current_state_info.get("times", [])
        if not times:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="尚未輸入任何時間，請至少選擇一個時間。"))
        else:
            try:
                required_fields = ["member", "medicine_name", "frequency_code", "dosage", "days"]
                missing = [f for f in required_fields if not current_state_info.get(f)]
                if missing:
                    line_bot_api.reply_message(reply_token, TextSendMessage(
                        text=f"❗ 資料不完整，缺少欄位：{', '.join(missing)}，請重新設定提醒流程。"
                    ))
                    return

                frequency_code = current_state_info["frequency_code"]
                frequency_name = get_frequency_name_by_code(frequency_code)

                add_medication_reminder_full(
                    recorder_id=line_user_id,
                    member=current_state_info["member"],
                    medicine_name=current_state_info["medicine_name"],
                    frequency_code=frequency_code,
                    dosage=current_state_info["dosage"],
                    days=current_state_info["days"],
                    times=times
                )

                clear_temp_state(line_user_id)
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=(
                        f"✅ 提醒已建立成功！\n"
                        f"👤 用藥對象：{current_state_info['member']}\n"
                        f"💊 藥品：{current_state_info['medicine_name']}\n"
                        f"🔁 頻率：{frequency_name}（{frequency_code}）\n"
                        f"📆 天數：{current_state_info['days']}\n"
                        f"🕒 時間：{', '.join(times)}"
                    )
                ))
            except Exception as e:
                app.logger.error(f"建立提醒失敗：{e}")
                traceback.print_exc()
                line_bot_api.reply_message(reply_token, TextSendMessage(text="❗ 建立提醒時發生錯誤，請稍後再試。"))


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