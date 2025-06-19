from linebot.models import (
    TextSendMessage, FlexSendMessage,
    QuickReply, QuickReplyButton,
    MessageAction, URIAction,
    PostbackAction, DatetimePickerAction
)

# 確保從 models 模組中導入所有需要的函數
from models import (
    create_user_if_not_exists,
    bind_family,
    generate_invite_code,
    clear_temp_state,      # 確保導入 clear_temp_state
    set_temp_state,        # 確保導入 set_temp_state
    get_medication_reminders_for_user, # 確保導入 get_medication_reminders_for_user
    get_temp_state # 確保導入 get_temp_state，用於檢查狀態
)

import re
from urllib.parse import quote

# 從 medication_reminder 導入需要的函數
from medication_reminder import (
    create_patient_selection_message, # 用於「新增用藥提醒」等需要選擇用藥者的入口
    create_medication_management_menu, # 用於「用藥管理」入口
    handle_ocr_recognition_result, # 處理 OCR 結果
    handle_medication_record_command, # 處理用藥記錄的起始指令
    handle_medication_record_member_selected,
    handle_medication_record_date_selected,
    handle_medication_record_medicine_name_input,
    handle_medication_record_dosage_selected,
    handle_medication_record_time_selected
)

from database import get_conn # 確保導入 get_conn

def create_usage_instructions_message():
    instructions = """
    「用藥提醒小幫手」功能說明：

    1. *新增用藥提醒：*
       - 點擊主選單中的「新增用藥提醒」。
       - 選擇您想設定提醒的家人。
       - 如果沒有家人，請先點擊「新增家人」。
       - 選擇提醒方式（手動輸入或上傳藥單照片）。
       - 依照指示輸入藥物名稱、頻率、時間和劑量。
       - 確認資訊後，用藥提醒就會設定完成。

    2. *用藥管理：*
       - 點擊主選單中的「用藥管理」。
       - 您可以選擇「編輯家人資料」來修改家人名稱。
       - 您也可以選擇「查看用藥提醒」來瀏覽已設定的提醒。
       - 在「查看用藥提醒」中，您可以選擇「修改提醒」或「刪除提醒」。

    3. *用藥記錄：*
       - 點擊主選單中的「用藥記錄」。
       - 選擇您想記錄用藥的家人。
       - 選擇用藥日期、輸入藥物名稱、選擇劑量和用藥時間。
       - 確認後，該次用藥記錄將會被儲存。

    4. *查看提醒：*
       - 點擊主選單中的「查看提醒」。
       - 選擇您想查看提醒的家人，系統將列出該家人的所有用藥提醒。

    5. *邀請家人：*
       - 點擊主選單中的「邀請家人」。
       - 系統會生成一個邀請碼，將此邀請碼分享給您的家人。
       - 家人綁定後，您就可以為他們設定用藥提醒和記錄。

    6. *綁定家人：*
       - 點擊主選單中的「綁定家人」。
       - 輸入您從家人那裡獲得的邀請碼，即可綁定成功。

    7. *聯絡我們：*
       - 點擊主選單中的「聯絡我們」。
       - 您將會看到開發團隊的聯絡資訊。

    如有其他問題，請隨時聯繫我們。
    """
    return TextSendMessage(text=instructions)

# 修改 handle_text_message 的函數簽名，移除 user_states
def handle_text_message(event, line_bot_api):
    reply_token = event.reply_token
    line_user_id = event.source.user_id
    message_text = event.message.text.strip()

    # 修改點：使用 get_temp_state 獲取狀態，並使用 or {} 確保返回字典
    current_state = get_temp_state(line_user_id) or {}
    state = current_state.get("state")

    # 處理 OCR 辨識結果的訊息 (假設這是在圖片訊息後傳送的文本)
    if state == "AWAITING_OCR_CONFIRMATION" and message_text in ["是", "否"]:
        handle_ocr_recognition_result(reply_token, line_user_id, message_text, line_bot_api)
        return

    # 處理「用藥記錄」相關的文字輸入
    if state == "AWAITING_MED_RECORD_MEMBER":
        handle_medication_record_member_selected(reply_token, line_bot_api, line_user_id, message_text)
        return
    elif state == "AWAITING_MED_RECORD_DATE":
        handle_medication_record_date_selected(reply_token, line_bot_api, line_user_id, message_text)
        return
    elif state == "AWAITING_MEDICINE_NAME":
        if current_state.get("record_date"):
            # 有 record_date，代表來自「新增用藥記錄」
            handle_medication_record_medicine_name_input(reply_token, line_bot_api, line_user_id, message_text)
        else:
            # 否則是來自「新增提醒」
            set_temp_state(line_user_id, {
                "state": "AWAITING_FREQUENCY_SELECTION",
                "member": current_state.get("member"),
                "medicine_name": message_text
        })
    elif state == "AWAITING_MED_RECORD_DOSAGE": # 從 OCR 流程跳轉過來手動輸入劑量
        handle_medication_record_dosage_selected(reply_token, line_bot_api, line_user_id, message_text)
        return
    elif state == "AWAITING_MED_RECORD_TIME": # 從 OCR 流程跳轉過來手動輸入時間
        handle_medication_record_time_selected(reply_token, line_bot_api, line_user_id, message_text)
        return
    elif state == "AWAITING_ADDITIONAL_DRUGS_CHOICE": # 詢問是否繼續新增藥品
        member = current_state.get("member")
        if message_text == "是":
            # 修改點：使用 set_temp_state
            set_temp_state(line_user_id, {"state": "AWAITING_MEDICINE_NAME", "member": member})
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"請輸入下一個藥品名稱或上傳藥單照片："))
        elif message_text == "否":
            # 修改點：使用 clear_temp_state
            clear_temp_state(line_user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="好的，已完成所有藥品提醒的設定。"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請回答「是」或「否」。"))
        return


    # 處理一般文字訊息

     # 新增處理「使用說明」的邏輯
    if message_text == "使用說明":
        message = create_usage_instructions_message()
        line_bot_api.reply_message(reply_token, message)
        return
    
    if message_text == "綁定":
        # 修改點：使用 set_temp_state
        set_temp_state(line_user_id, {"state": "AWAITING_INVITE_CODE"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="好的，請輸入您收到的邀請碼："))

    elif state == "AWAITING_INVITE_CODE":
        invite_code = message_text
        # 修改點：使用 clear_temp_state
        clear_temp_state(line_user_id)
        try:
            # 嘗試綁定家庭
            if bind_family(invite_code, line_user_id):
                line_bot_api.reply_message(reply_token, TextSendMessage(text="綁定成功！您現在可以看到家庭成員的用藥提醒了。"))
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="綁定失敗，邀請碼無效或已過期。"))
        except Exception as e:
            print(f"Error binding family: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="綁定過程中發生錯誤，請稍後再試。"))

    elif message_text == "解除綁定":
        # 修改點：使用 set_temp_state
        set_temp_state(line_user_id, {"state": "AWAITING_UNBIND_CONFIRMATION"})
        line_bot_api.reply_message(reply_token, TextSendMessage(text="您確定要解除家庭綁定嗎？請輸入「是」或「否」。"))

    elif state == "AWAITING_UNBIND_CONFIRMATION":
        if message_text == "是":
            try:
                # 假設這裡有解除綁定的邏輯，例如刪除 invitation_recipients 表中的記錄
                # 由於沒有提供解除綁定的具體函數，這裡只清空狀態
                # 實作時需要呼叫實際的解除綁定函數
                clear_temp_state(line_user_id) # 修改點：使用 clear_temp_state
                line_bot_api.reply_message(reply_token, TextSendMessage(text="已解除家庭綁定。"))
            except Exception as e:
                print(f"Error unbinding family: {e}")
                line_bot_api.reply_message(reply_token, TextSendMessage(text="解除綁定失敗，請稍後再試。"))
        elif message_text == "否":
            # 修改點：使用 clear_temp_state
            clear_temp_state(line_user_id)
            line_bot_api.reply_message(reply_token, TextSendMessage(text="已取消解除綁定。"))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請回答「是」或「否」。"))

    elif message_text == "用藥管理":
        # 這裡會重複 app.py 的處理，但作為 fallback 可以保留
        line_bot_api.reply_message(reply_token, create_medication_management_menu(line_user_id))

    elif message_text == "新增用藥提醒":
        # 修改點：使用 set_temp_state
        set_temp_state(line_user_id, {"state": "AWAITING_PATIENT_FOR_REMINDER"})
        line_bot_api.reply_message(reply_token, create_patient_selection_message(line_user_id))

    elif message_text == "查看提醒":
        flex_message = create_patient_selection_for_reminders_view(line_user_id)
        if flex_message:
            line_bot_api.reply_message(reply_token, flex_message)
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="目前沒有任何家人需要查看提醒。"))

    elif message_text == "新增用藥記錄":
        # 調用 medication_reminder 模組中的函數來處理
        handle_medication_record_command(reply_token, line_bot_api, line_user_id)

    else:
        # 其他未知的文字訊息
        line_bot_api.reply_message(reply_token, TextSendMessage(text="抱歉，我不太明白您的意思。您可以嘗試輸入「綁定」或「用藥管理」等指令。"))


# 這是 create_patient_selection_for_reminders_view 的實現，用於「查看提醒」
def create_patient_selection_for_reminders_view(line_id):
    conn = get_conn()
    if not conn:
        return TextSendMessage(text="抱歉，無法連接到使用者資料庫。")
    items = []
    try:
        cursor = conn.cursor(dictionary=True)
        # 修改點：users 表格的 primary key 是 recorder_id，所以直接用 line_id 查詢
        cursor.execute("SELECT recorder_id FROM users WHERE recorder_id = %s", (line_id,))
        user = cursor.fetchone()
        if not user:
            return TextSendMessage(text="找不到您的使用者資料。")
        recorder_id_for_db = user['recorder_id'] # 使用 recorder_id

        # 修改點：patient 表格是 recorder_id 和 member
        cursor.execute("SELECT member FROM patients WHERE recorder_id = %s ORDER BY member", (recorder_id_for_db,))
        existing_patients = cursor.fetchall()

        if not existing_patients:
            return TextSendMessage(text="您目前沒有任何用藥對象可以查看提醒。")

        for patient in existing_patients:
            items.append(
                QuickReplyButton(
                    action=PostbackAction(
                        label=f"查看「{patient['member']}」",
                        data=f"action=show_reminders_for_member&member={quote(patient['member'])}", # 修改 action data
                        display_text=f"查看「{patient['member']}」的提醒"
                    )
                )
            )
        return TextSendMessage(text="請選擇您想查看提醒的家人：", quick_reply=QuickReply(items=items))

    except Exception as e:
        print(f"Error in create_patient_selection_for_reminders_view: {e}")
        return TextSendMessage(text="抱歉，在讀取用藥者資訊時發生錯誤。")
    finally:
        if conn and conn.is_connected():
            conn.close()