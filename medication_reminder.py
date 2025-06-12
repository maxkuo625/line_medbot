import datetime
from database import get_conn
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton,
    DatetimePickerAction, MessageAction, PostbackAction
)
from linebot.exceptions import LineBotApiError
from models import (
    get_all_family_user_ids, get_medicine_list,
    get_temp_state, set_temp_state, clear_temp_state,
    add_medication_reminder, delete_medication_reminder, get_medication_reminders_for_user
)

# -------------------------------------------------------------
# 定義劑量 Quick Reply 選項
# -------------------------------------------------------------
DOSAGE_OPTIONS = [
    {'label': '1 錠', 'data': '1 錠'},
    {'label': '1 顆', 'data': '1 顆'},
    {'label': '1 毫升(ml)', 'data': '1 ml'},
    {'label': '5 毫升(ml)', 'data': '5 ml'},
    {'label': '1 包', 'data': '1 包'},
    {'label': '半顆', 'data': '半顆'},
    {'label': '2 錠', 'data': '2 錠'},
    {'label': '其他', 'data': '其他劑量'} # 讓用戶輸入的選項
]

# -------------------------------------------------------------
# 定時提醒函式 (run_reminders 修改為合併訊息)
# -------------------------------------------------------------
def run_reminders(line_bot_api):
    now = datetime.datetime.now().strftime('%H:%M')
    print(f"DEBUG: Checking reminders for time: {now}")

    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT um.user_id, m.name AS medicine_name, um.dosage, um.frequency
        FROM user_medication um
        JOIN medicines m ON um.medicine_id = m.id
        WHERE um.time_str = %s
        """,
        (now,)
    )
    results = cursor.fetchall()
    cursor.close()
    conn.close()

    # 建立一個字典來按 user_id 分組提醒
    reminders_by_user = {}
    for row in results:
        owner_id = row["user_id"]
        if owner_id not in reminders_by_user:
            reminders_by_user[owner_id] = []
        reminders_by_user[owner_id].append({
            'medicine_name': row["medicine_name"],
            'dosage': row.get("dosage", "未指定劑量"),
            'frequency': row.get("frequency", "未指定頻率")
        })

    notified_overall = set() # 用於追蹤哪些用戶已經被通知過，避免重複

    for owner_id, meds_to_take in reminders_by_user.items():
        all_family_user_ids = get_all_family_user_ids(owner_id)
        
        try:
            profile = line_bot_api.get_profile(owner_id)
            owner_name = profile.display_name
        except LineBotApiError as e:
            print(f"ERROR: Could not get profile for {owner_id}: {e}")
            owner_name = "家人"

        # 構建合併後的訊息
        med_list_str = []
        for med in meds_to_take:
            med_list_str.append(
                f"- {med['medicine_name']} (劑量: {med['dosage']})"
            )
        
        # 這是給擁有者自己的訊息
        owner_msg = (
            f"⏰ 用藥提醒：現在是 {now}，請記得服用以下藥品：\n" +
            "\n".join(med_list_str)
        )
        
        # 這是給家庭成員的訊息
        family_msg = (
            f"⏰ {owner_name} 的用藥提醒：現在是 {now}，請記得提醒對方服用以下藥品：\n" +
            "\n".join(med_list_str)
        )

        for uid in all_family_user_ids:
            if uid in notified_overall:
                print(f"DEBUG: Skipping already notified UID: {uid} in family {owner_id}'s reminder.")
                continue
            
            msg_to_send = ""
            if uid == owner_id:
                msg_to_send = owner_msg
            else:
                msg_to_send = family_msg
            
            print(f"DEBUG: Attempting to push combined message to UID: {uid} for owner {owner_id}: {msg_to_send}")
            try:
                line_bot_api.push_message(uid, TextSendMessage(text=msg_to_send))
                notified_overall.add(uid) # 將已通知的用戶加入集合
                print(f"DEBUG: Successfully pushed combined message to {uid}")
            except Exception as e:
                print(f"ERROR: Failed to push combined message to {uid}: {e}")


# -------------------------------------------------------------
# 處理文字訊息 (handle_medication_command)
# -------------------------------------------------------------
def handle_medication_command(event, line_bot_api):
    user_id = event.source.user_id
    text = ""
    if hasattr(event.message, "text"):
        text = event.message.text.strip()

    temp_state = get_temp_state(user_id)
    print(f"DEBUG(handle_medication_command): User: {user_id}, Text: '{text}', Temp State: {temp_state}")

    # 處理多步驟流程中的文字輸入 (其他劑量)
    if temp_state and temp_state.get('state') == 'awaiting_custom_dosage':
        # 用戶輸入了自定義劑量
        temp_state['dosage'] = text
        # 更新狀態為等待時間選擇
        temp_state['state'] = 'selecting_time'
        set_temp_state(user_id, temp_state)
        # 進入選擇時間的步驟
        medicine_name = temp_state.get('medicine_name', '未知藥品')
        dosage = temp_state.get('dosage')
        send_time_selection(user_id, event.reply_token, line_bot_api, medicine_name, dosage, initial_message=True)
        return True
    
    # 處理完成用藥時間設定的文字指令
    if text == "完成用藥時間設定" and temp_state and temp_state.get('state') == 'selecting_time':
        clear_temp_state(user_id)
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="✅ 已完成所有用藥提醒設定。"))
        return True

    # 處理其他多步驟的引導訊息
    elif temp_state and temp_state.get('state') == 'selecting_medicine' and text not in ["用藥時間設定", "新增用藥提醒", "查詢用藥時間", "刪除用藥時間"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請從下方選擇藥品。"))
        return True
    elif temp_state and temp_state.get('state') == 'selecting_dosage' and text not in ["用藥時間設定", "新增用藥提醒", "查詢用藥時間", "刪除用藥時間"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請從下方選擇劑量。"))
        return True
    elif temp_state and temp_state.get('state') == 'selecting_time' and text not in ["用藥時間設定", "新增用藥提醒", "查詢用藥時間", "刪除用藥時間", "完成用藥時間設定"]:
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請選擇用藥時間或點擊「完成設定」。"))
        return True


    # ✅ 主選單：用藥時間設定
    if text == "用藥時間設定":
        clear_temp_state(user_id)
        message = TextSendMessage(
            text="請選擇您的操作：",
            quick_reply=QuickReply(
                items=[
                    QuickReplyButton(action=MessageAction(label="新增用藥提醒", text="新增用藥提醒")),
                    QuickReplyButton(action=MessageAction(label="查詢用藥時間", text="查詢用藥時間")),
                    QuickReplyButton(action=MessageAction(label="刪除用藥時間", text="刪除用藥時間"))
                ]
            )
        )
        line_bot_api.reply_message(event.reply_token, message)
        return True

    # ✅ 新增用藥提醒 - 步驟1: 選擇藥品
    if text == "新增用藥提醒":
        medicines = get_medicine_list()
        if not medicines:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="目前沒有可設定的藥品。"))
            return True

        buttons = [
            QuickReplyButton(
                action=PostbackAction(
                    label=med['name'],
                    data=f"action=select_medicine&medicine_id={med['id']}&medicine_name={med['name']}"
                )
            )
            for med in medicines
        ]
        set_temp_state(user_id, {'state': 'selecting_medicine'})
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請選擇您要設定提醒的藥品：",
                quick_reply=QuickReply(items=buttons[:13])
            )
        )
        return True

    # ✅ 查詢用藥時間
    if text == "查詢用藥時間":
        clear_temp_state(user_id)
        reminders = get_medication_reminders_for_user(user_id) 
        if reminders:
            reply_lines = ["📋 您已設定的用藥提醒有："]
            for item in reminders:
                dosage_info = f"劑量：{item.get('dosage', '未指定')}" if item.get('dosage') else ""
                frequency_info = f"頻率：{item.get('frequency', '未指定')}" if item.get('frequency') else ""
                
                detail_info = []
                if dosage_info: detail_info.append(dosage_info)
                if frequency_info: detail_info.append(frequency_info)
                
                details_str = ", ".join(detail_info) if detail_info else "無額外資訊"

                reply_lines.append(f"- {item['time_str']}：{item['medicine_name']} ({details_str})")
            reply = "\n".join(reply_lines)
        else:
            reply = "❗ 尚未設定任何用藥提醒，請輸入「新增用藥提醒」。"
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply))
        return True

    # ✅ 刪除用藥時間
    if text == "刪除用藥時間":
        clear_temp_state(user_id)
        reminders = get_medication_reminders_for_user(user_id)
        if not reminders:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 沒有可刪除的用藥提醒。"))
            return True

        buttons = []
        for item in reminders:
            # --- 【修正】 縮短 label 長度以符合 Line API 限制 (20 字元) ---
            display_medicine_name = item['medicine_name']
            # HH:MM - (8字元) + "..." (3字元) = 11字元
            # 剩餘 9 字元給藥品名稱
            if len(display_medicine_name) > 9:
                display_medicine_name = display_medicine_name[:9] + "..."
            
            label = f"{item['time_str']} - {display_medicine_name}"
            # -------------------------------------------------------------
            
            # 傳遞完整的資訊以便精確刪除
            buttons.append(
                QuickReplyButton(
                    action=PostbackAction(
                        label=label,
                        data=f"action=delete_reminder&time={item['time_str']}&medicine_id={item['medicine_id']}"
                    )
                )
            )
        line_bot_api.reply_message(
            event.reply_token,
            TextSendMessage(
                text="請選擇要刪除的用藥提醒：",
                quick_reply=QuickReply(items=buttons[:13])
            )
        )
        return True
    return False

# -------------------------------------------------------------
# 處理 Postback 事件 (handle_postback) - 調整邏輯以適應新步驟
# -------------------------------------------------------------
def handle_postback(event, line_bot_api):
    user_id = event.source.user_id
    data = event.postback.data
    temp_state = get_temp_state(user_id)
    print(f"DEBUG(handle_postback): User: {user_id}, Data: '{data}', Temp State: {temp_state}")

    # 處理刪除用藥提醒
    if data.startswith("action=delete_reminder"):
        clear_temp_state(user_id)
        params = dict(item.split("=") for item in data.split("&")[1:])
        time_to_delete = params.get("time")
        medicine_id_to_delete = params.get("medicine_id")
        
        if time_to_delete and medicine_id_to_delete:
            delete_medication_reminder(user_id, medicine_id_to_delete, time_to_delete)
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text=f"✅ 已刪除 {time_to_delete} 的用藥提醒。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 刪除失敗，請再試一次。"))
        return True

    # 處理選擇藥品 -> 進入選擇劑量步驟
    if data.startswith("action=select_medicine") and temp_state and temp_state.get('state') == 'selecting_medicine':
        params = dict(item.split("=") for item in data.split("&")[1:])
        medicine_id = params.get("medicine_id")
        medicine_name = params.get("medicine_name")

        if medicine_id and medicine_name:
            # 更新暫存狀態，進入選擇劑量階段
            set_temp_state(user_id, {
                'state': 'selecting_dosage',
                'medicine_id': medicine_id,
                'medicine_name': medicine_name
            })
            send_dosage_selection(user_id, event.reply_token, line_bot_api, medicine_name)
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 選擇藥品失敗，請再試一次。"))
        return True

    # 處理選擇劑量 -> 進入選擇時間步驟
    if data.startswith("action=select_dosage") and temp_state and temp_state.get('state') == 'selecting_dosage':
        params = dict(item.split("=") for item in data.split("&")[1:])
        selected_dosage = params.get("dosage")

        if selected_dosage:
            temp_state['dosage'] = selected_dosage
            # 如果選擇「其他」，進入等待用戶輸入自定義劑量的狀態
            if selected_dosage == '其他劑量':
                temp_state['state'] = 'awaiting_custom_dosage'
                set_temp_state(user_id, temp_state)
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="請輸入您要設定的劑量："))
                return True
            
            # 更新狀態為等待時間選擇 (多選時間)
            temp_state['state'] = 'selecting_time'
            set_temp_state(user_id, temp_state)
            medicine_name = temp_state.get('medicine_name', '未知藥品')
            dosage = temp_state.get('dosage')
            send_time_selection(user_id, event.reply_token, line_bot_api, medicine_name, dosage, initial_message=True)
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 選擇劑量失敗，請再試一次。"))
        return True

    # 處理 DatetimePicker 設定時間 -> 儲存並重新詢問或完成
    if data == "action=set_med_time" and event.postback.params:
        selected_time = event.postback.params.get("time")
        if selected_time and temp_state and temp_state.get('state') == 'selecting_time':
            medicine_id = temp_state.get('medicine_id')
            medicine_name = temp_state.get('medicine_name', '未知藥品')
            dosage = temp_state.get('dosage')
            # 頻率固定為「多個時段」，表示用戶可自訂多個時間點
            frequency = "多個時段" 

            if medicine_id and medicine_name and dosage:
                # 儲存完整的提醒信息，包括劑量和頻率
                print(f"DEBUG: Adding reminder for {user_id}: {medicine_name} at {selected_time}, Dosage: {dosage}, Frequency: {frequency}")
                add_medication_reminder(user_id, medicine_id, selected_time, dosage, frequency)
                # 不清除暫存狀態，而是重新發送時間選擇訊息，讓用戶可以繼續新增時間
                send_time_selection(user_id, event.reply_token, line_bot_api, medicine_name, dosage, initial_message=False)
            else:
                line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 儲存失敗，請從頭開始設定。"))
        else:
            line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 設定時間失敗，請再試一次。"))
        return True
    return False


# -------------------------------------------------------------
# 輔助函式：用於發送 Quick Reply
# -------------------------------------------------------------
def send_dosage_selection(user_id, reply_token, line_bot_api, medicine_name):
    print(f"DEBUG: Entering send_dosage_selection. User: {user_id}, Medicine: {medicine_name}")
    buttons = []
    for option in DOSAGE_OPTIONS:
        buttons.append(
            QuickReplyButton(
                action=PostbackAction(
                    label=option['label'],
                    data=f"action=select_dosage&dosage={option['data']}"
                )
            )
        )
    try:
        line_bot_api.reply_message(
            reply_token,
            TextSendMessage(
                text=f"您選擇了「{medicine_name}」。\n請選擇劑量：",
                quick_reply=QuickReply(items=buttons)
            )
        )
        print(f"DEBUG: Successfully sent dosage selection message to {user_id}")
    except LineBotApiError as e:
        print(f"ERROR: Failed to send dosage selection message to {user_id}: {e}")
    except Exception as e:
        print(f"CRITICAL ERROR in send_dosage_selection for {user_id}: {e}")


def send_time_selection(user_id, reply_token, line_bot_api, medicine_name, dosage, initial_message=True):
    print(f"DEBUG: Entering send_time_selection. User: {user_id}, Medicine: {medicine_name}, Dosage: {dosage}")
    text_message = ""
    if initial_message:
        text_message = f"您已設定藥品「{medicine_name}」，劑量「{dosage}」。\n請選擇用藥時間，或點擊「完成設定」結束："
    else:
        text_message = f"已成功新增一個用藥時間。還有其他時間嗎？請選擇用藥時間，或點擊「完成設定」結束："

    message = TextSendMessage(
        text=text_message,
        quick_reply=QuickReply(
            items=[
                QuickReplyButton(
                    action=DatetimePickerAction(
                        label="選擇時間",
                        data="action=set_med_time",
                        mode="time",
                        initial="08:00"
                    )
                ),
                QuickReplyButton(
                    action=MessageAction(label="完成設定", text="完成用藥時間設定")
                )
            ]
        )
    )
    try:
        line_bot_api.reply_message(reply_token, message)
        print(f"DEBUG: Successfully sent time selection message to {user_id}")
    except LineBotApiError as e:
        print(f"ERROR: Failed to send time selection message to {user_id}: {e}")
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text="抱歉，設定時間時發生錯誤，請重新開始。"))
        except Exception as push_e:
            print(f"ERROR: Also failed to push error message: {push_e}")
    except Exception as e:
        print(f"CRITICAL ERROR in send_time_selection for {user_id}: {e}")
        try:
            line_bot_api.push_message(user_id, TextSendMessage(text="抱歉，設定時間時發生未知錯誤，請重新開始。"))
        except Exception as push_e:
            print(f"ERROR: Also failed to push error message: {push_e}")