from datetime import datetime
import re
import json
from urllib.parse import quote, parse_qs

from database import get_conn
from linebot.models import (
    TextSendMessage, QuickReply, QuickReplyButton,
    DatetimePickerAction, MessageAction, PostbackAction
)
from linebot.exceptions import LineBotApiError
from models import (
    get_all_family_user_ids, get_medicine_list,
    get_temp_state, set_temp_state, clear_temp_state,
    delete_medication_reminder_time,
    get_medication_reminders_for_user,
    get_medicine_id_by_name,
    add_medication_record,
    get_frequency_name,
    add_medication_reminder_full,
    create_user_if_not_exists,
    get_all_frequency_options,
    get_reminder_times_for_user
)
import logging # For logging

logging.basicConfig(level=logging.INFO)

# -------------------------------------------------------------
# 定義劑量 Quick Reply 選項 (Existing code)
# -------------------------------------------------------------
DOSAGE_OPTIONS = [
    {'label': '1 錠', 'data': '1 錠'},
    {'label': '1 顆', 'data': '1 顆'},
    {'label': '1 毫升(ml)', 'data': '1 ml'},
    {'label': '5 毫升(ml)', 'data': '5 ml'},
    {'label': '1 包', 'data': '1 包'},
    {'label': '半顆', 'data': '半顆'},
    {'label': '2 錠', 'data': '2 錠'}, # Added this based on common dosages
    {'label': '其他', 'data': '其他'}
]

def create_frequency_quickreply():
    try:
        frequency_options = get_all_frequency_options()  # List of tuples (code, name)
        buttons = [
            QuickReplyButton(
                action=PostbackAction(
                    label=name,
                    data=f"action=set_frequency_val&val={code}"
                )
            ) for code, name in frequency_options
        ]
        return QuickReply(items=buttons)
    except Exception as e:
        print(f"取得頻率選單失敗: {e}")
        return QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label="一日一次", data="action=set_frequency_val&val=QD"))
        ])

# ------------------------------------------------------------
# 執行用藥提醒
# ------------------------------------------------------------
def run_reminders(line_bot_api):
    logging.info(f"正在執行提醒任務，當前時間: {datetime.now().strftime('%H:%M')}")
    conn = get_conn()
    if not conn:
        logging.error("無法連接到資料庫，跳過提醒任務。")
        return

    try:
        cursor = conn.cursor(dictionary=True)
        current_time_str = datetime.now().strftime('%H:%M:%S')
        display_time = datetime.now().strftime('%H:%M')

        query = """
        SELECT
            rt.recorder_id AS line_user_id,
            rt.member,
            fc.frequency_name,
            mr.dose_quantity,
            di.drug_name_zh AS medicine_name
        FROM
            reminder_time rt
        JOIN
            medication_record mr ON rt.recorder_id = mr.recorder_id
                                AND rt.member = mr.member
                                AND rt.frequency_name = mr.frequency_count_code
        LEFT JOIN
            drug_info di ON mr.drug_name_zh = di.drug_name_zh
        JOIN
            frequency_code fc ON rt.frequency_name = fc.frequency_code
        WHERE
            TIME(%s) IN (
                TIME(rt.time_slot_1),
                TIME(rt.time_slot_2),
                TIME(rt.time_slot_3)
            )
        """

        cursor.execute(query, (current_time_str,))
        reminders = cursor.fetchall()

        if not reminders:
            logging.info("目前沒有需要發送的提醒。")
            return

        for reminder in reminders:
            line_user_id = reminder["line_user_id"]
            member = reminder["member"]
            medicine_name = reminder["medicine_name"] or "（未命名藥品）"
            frequency_name = reminder["frequency_name"]
            dose_quantity = reminder["dose_quantity"]
            # dosage_unit = reminder.get("dosage_unit") or ""
            dose_str = f"{dose_quantity}" if dose_quantity else "未提供"

            message_text = (
                f"🔔 用藥時間到囉！\n"
                f"👤 用藥者：{member}\n"
                f"💊 藥品：{medicine_name}\n"
                f"⏰ 頻率：{frequency_name}\n"
                f"💊 劑量：{dose_str}\n"
                f"⏰ 時間：{display_time}\n"
                f"請記得按時服用喔！"
            )

            line_bot_api.push_message(line_user_id, TextSendMessage(text=message_text))

    except Exception as e:
        logging.error(f"執行提醒任務時發生錯誤: {e}")
    finally:
        conn.close()


# ------------------------------------------------------------
# 用藥者管理相關功能
# ------------------------------------------------------------

def create_patient_selection_message(line_id: str, context: str = None): # Modified signature
    conn = get_conn()
    if not conn:
        return TextSendMessage(text="抱歉，無法連接到使用者資料庫。")
    items = []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT recorder_id FROM users WHERE recorder_id = %s", (line_id,))
        user = cursor.fetchone()
        
        if not user:
            cursor.execute("INSERT INTO users (recorder_id, user_name) VALUES (%s, %s)", (line_id, "新用戶"))
            conn.commit()
            current_recorder_id = line_id
            
            cursor.execute("INSERT INTO patients (recorder_id, member) VALUES (%s, %s)", (current_recorder_id, "本人"))
            conn.commit()
            existing_patients = [{'member': '本人'}]
        else:
            current_recorder_id = user['recorder_id']
            cursor.execute("SELECT member FROM patients WHERE recorder_id = %s", (current_recorder_id,))
            existing_patients = cursor.fetchall()
            
            if not existing_patients:
                cursor.execute("INSERT INTO patients (recorder_id, member) VALUES (%s, %s)", (current_recorder_id, "本人"))
                conn.commit()
                existing_patients = [{'member': '本人'}]

        for patient in existing_patients:
            postback_data = f"action=select_patient_for_reminder&member={quote(patient['member'])}"
            display_text_label = f"選擇 {patient['member']}" # Default display text

            if context: # Only append context if it's provided
                postback_data += f"&context={context}"

            if context == "add_reminder":
                display_text_label = f"為「{patient['member']}」新增提醒"
            elif context == "query_reminder":
                display_text_label = f"查詢「{patient['member']}」的提醒"
            elif context == "manage_reminders":
                display_text_label = f"管理「{patient['member']}」的提醒"

            items.append(
                QuickReplyButton(
                    action=PostbackAction(
                        label=patient['member'],
                        data=postback_data,
                        display_text=display_text_label
                    )
                )
            )
        if len(existing_patients) < 4:
            items.append(
                QuickReplyButton(
                    action=PostbackAction(
                        label="⊕ 新增家人",
                        data="action=add_new_patient",
                        display_text="新增家人"
                    )
                )
            )
    except Exception as e:
        logging.error(f"Error in create_patient_selection_message: {e}")
        import traceback
        traceback.print_exc()
        return TextSendMessage(text="抱歉，在讀取用藥者資訊時發生錯誤。")
    finally:
        if conn and conn.is_connected():
            conn.close()

    return TextSendMessage(
        text="請問這份藥單是給誰的？" if context == "add_reminder" else
             "請問您想查詢誰的用藥時間？" if context == "query_reminder" else
             "請問您想管理誰的用藥提醒？" if context == "manage_reminders" else
             "請選擇用藥對象：", # Default if context is not specifically handled
        quick_reply=QuickReply(items=items)
    )

def create_medication_management_menu(line_id: str):
    items = [
        QuickReplyButton(
            action=PostbackAction(
                label="選擇家人/照顧對象",
                data="action=select_patient_for_reminder_initial", # New action to go to selection menu
                display_text="選擇用藥對象"
            )
        ),
        QuickReplyButton(
            action=PostbackAction(
                label="修改家人名稱",
                data="action=show_patient_edit_menu",
                display_text="修改家人名稱"
            )
        )
    ]

    conn = get_conn()
    if conn:
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT recorder_id FROM users WHERE recorder_id = %s", (line_id,))
            user = cursor.fetchone()
            if user:
                recorder_id_for_query = user['recorder_id']
                cursor.execute("SELECT count(*) as count FROM patients WHERE recorder_id = %s", (recorder_id_for_query,)) # 修改點
                patient_count = cursor.fetchone()['count']
                if patient_count < 4:
                     items.append(
                        QuickReplyButton(
                            action=PostbackAction(
                                label="⊕ 新增家人",
                                data="action=add_new_patient",
                                display_text="新增家人"
                            )
                        )
                    )
        except Exception as e:
            logging.error(f"Error checking patient count for management menu: {e}")
        finally:
            if conn.is_connected():
                conn.close()

    return TextSendMessage(text="請問您要進行哪種用藥管理操作？", quick_reply=QuickReply(items=items))


def create_patient_edit_message(line_id: str):
    conn = get_conn()
    if not conn:
        return TextSendMessage(text="抱歉，無法連接到使用者資料庫。")
    items = []
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT recorder_id FROM users WHERE recorder_id = %s", (line_id,))
        user = cursor.fetchone()
        if not user:
            return TextSendMessage(text="找不到您的使用者資料。")
        recorder_id_for_query = user['recorder_id']
        cursor.execute("SELECT member FROM patients WHERE recorder_id = %s AND member != '本人' ORDER BY member", (recorder_id_for_query,))
        editable_patients = cursor.fetchall()
        if not editable_patients:
            return TextSendMessage(text="您目前沒有可供修改的家人名單喔！")
        for patient in editable_patients:
            items.append(
                QuickReplyButton(
                    action=PostbackAction(
                        label=f"修改「{patient['member']}」",
                        data=f"action=edit_patient_start&member_to_edit={quote(patient['member'])}",
                        display_text=f"我想修改「{patient['member']}」的名稱"
                    )
                )
            )
    except Exception as e:
        logging.error(f"Error in create_patient_edit_message: {e}")
        return TextSendMessage(text="抱歉，在讀取家人名單時發生錯誤。")
    finally:
        if conn and conn.is_connected():
            conn.close()
    return TextSendMessage(text="請問您想修改哪一位家人的名稱？", quick_reply=QuickReply(items=items))


def get_patient_id_by_member_name(line_id: str, member_name: str):
    conn = get_conn()
    if not conn:
        return None
    try:
        cursor = conn.cursor()
        # Change: Removed patient_id selection. We only need to confirm existence.
        cursor.execute("SELECT recorder_id FROM patients WHERE recorder_id = %s AND member = %s", (line_id, member_name))
        patient_record = cursor.fetchone()
        # Return True if patient exists, False otherwise
        return True if patient_record else False
    except Exception as e:
        logging.error(f"Error in get_patient_id_by_member_name: {e}")
        return None
    finally:
        if conn and conn.is_connected():
            conn.close()


def _display_medication_reminders(reply_token, line_bot_api, line_user_id, member):
    from models import get_reminder_times_for_user, delete_medication_reminder_time  # 確保匯入

    conn = get_conn()
    if not conn:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ 資料庫連線失敗，請稍後再試。"))
        return

    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT recorder_id, member FROM patients WHERE recorder_id = %s AND member = %s",
            (line_user_id, member)
        )
        patient = cursor.fetchone()
        if not patient:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到「{member}」的用藥者資料。"))
            return

        # ✅ 改為從 reminder_time 抓資料
        reminders = get_reminder_times_for_user(line_user_id, member)
        if not reminders:
            line_bot_api.reply_message(reply_token, TextSendMessage(text=f"「{member}」目前沒有設定任何用藥提醒。"))
            return

        reminder_messages = []
        quick_reply_buttons = []

        for r in reminders:
            frequency_name = r.get('frequency_name', '未知頻率')

            # 將時間欄位轉為字串
            times = []
            for i in range(1, 5):
                raw_time = r.get(f'time_slot_{i}')
                if raw_time:
                    if isinstance(raw_time, str):
                        times.append(raw_time)
                    elif hasattr(raw_time, 'strftime'):
                        times.append(raw_time.strftime('%H:%M'))
                    else:
                        times.append(str(raw_time))
            time_str = '、'.join(times) if times else '未設定'

            reminder_messages.append(f"頻率：{frequency_name}\n時間：{time_str}")

            # 🔘 為每個頻率新增一個刪除按鈕
            quick_reply_buttons.append(
                QuickReplyButton(
                    action=PostbackAction(
                        label=f"刪除 {frequency_name}",
                        data=f"action=delete_single_reminder&member={quote(member)}&frequency_name={quote(frequency_name)}"
                    )
                )
            )

        # 組裝最終訊息
        message = TextSendMessage(
            text=f"「{member}」的用藥提醒：\n" + "\n---\n".join(reminder_messages),
            quick_reply=QuickReply(items=quick_reply_buttons)
        )

        line_bot_api.reply_message(reply_token, message)
        clear_temp_state(line_user_id)

    except Exception as e:
        logging.error(f"Error displaying reminders for member {member}: {e}")
        import traceback
        traceback.print_exc()
        line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 查詢提醒失敗，請稍後再試。"))

    finally:
        if conn and conn.is_connected():
            conn.close()



# ------------------------------------------------------------
# 處理 OCR 辨識結果並引導使用者設定提醒 (Existing code)
# ------------------------------------------------------------
def handle_ocr_recognition_result(reply_token, line_bot_api, user_id, parsed_data):
    """
    處理 OCR 辨識出的藥單資訊，引導使用者設定用藥提醒。
    """
    if not parsed_data or not parsed_data.get('medicine_name') or not parsed_data.get('frequency_code'):
        line_bot_api.reply_message(reply_token, TextSendMessage(text="藥單辨識結果不完整，請嘗試重新拍照或手動輸入。"))
        clear_temp_state(user_id)
        return

    # Assuming 'member' is already stored in temp_state or passed from initial patient selection
    temp_state = get_temp_state(user_id)
    selected_member = temp_state.get("member")

    if not selected_member:
        # This case should ideally not happen if patient selection is enforced
        line_bot_api.reply_message(reply_token, TextSendMessage(text="請先選擇用藥對象。"))
        return

    set_temp_state(user_id, {
        "state": "AWAITING_MED_FREQUENCY",
        "member": selected_member,
        "medicine_name": parsed_data['medicine_name'],
        "dosage": parsed_data.get('dosage', '未設定'),
        "frequency_code": parsed_data['frequency_code'],
        "days": parsed_data.get('days'),
        "source_detail": "OCR_Scan"
    })
    # Proceed to ask for frequency confirmation or directly to time if frequency is clear
    frequency_name = parsed_data.get('frequency_name', get_frequency_name(parsed_data['frequency_code']))
    message = TextSendMessage(
        text=f"已辨識藥品名稱為：{parsed_data['medicine_name']}。\n"
             f"頻率：{frequency_name}。\n"
             f"請問這個資訊正確嗎？",
        quick_reply=QuickReply(items=[
            QuickReplyButton(
                action=PostbackAction(label="正確", data="action=confirm_ocr_frequency_correct")
            ),
            QuickReplyButton(
                action=PostbackAction(label="修改頻率", data="action=set_frequency")
            )
        ])
    )
    line_bot_api.reply_message(reply_token, message)

def handle_medication_record_time_selected(reply_token, line_bot_api, user_id, time_slot_input):
    current_state = get_temp_state(user_id) or {}
    member = current_state.get("member")
    medicine_name = current_state.get("medicine_name")
    dosage = current_state.get("dosage")
    record_date = current_state.get("record_date")

    if not all([member, medicine_name, dosage, record_date]):
        clear_temp_state(user_id)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="用藥記錄資訊不完整，請重新開始。"))
        return

    # 嘗試將輸入的時間轉換為 H:M 格式
    match = re.match(r'^(\d{1,2})[時點:](\d{2})$', time_slot_input)
    if not match:
        line_bot_api.reply_message(reply_token, TextSendMessage(text="時間格式不正確，請輸入 HH:MM 格式，例如 14:30 或 8點30。"))
        return

    hour = int(match.group(1))
    minute = int(match.group(2))

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        line_bot_api.reply_message(reply_token, TextSendMessage(text="時間無效，小時應在 0-23 之間，分鐘應在 0-59 之間。"))
        return

    # 將日期和時間組合成完整的 datetime 物件
    try:
        record_datetime_str = f"{record_date} {hour:02d}:{minute:02d}:00"
        record_datetime = datetime.strptime(record_datetime_str, '%Y-%m-%d %H:%M:%S')
    except ValueError as e:
        logging.error(f"Error parsing record_datetime: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="日期或時間格式轉換失敗，請稍後再試。"))
        return

    # 從資料庫獲取 drug_id
    drug_id_result = get_medicine_id_by_name(medicine_name)
    if not drug_id_result:
        line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到藥品「{medicine_name}」的資訊，請檢查藥品名稱。"))
        clear_temp_state(user_id)
        return
    drug_id = drug_id_result['drug_id']

    # 嘗試將 dosage 分割為數量和單位
    dose_quantity = None
    dosage_unit = None
    dose_match = re.match(r'([\d.]+)\s*(\S+)', dosage) # 例如 "1 錠"
    if dose_match:
        try:
            dose_quantity = float(dose_match.group(1))
            dosage_unit = dose_match.group(2).strip()
        except ValueError:
            pass # 如果轉換失敗，就保持 None

    if dose_quantity is None: # 如果無法解析，嘗試直接作為 quantity，單位留空
        try:
            dose_quantity = float(dosage)
        except ValueError:
            dose_quantity = None # 最終還是無法解析，保持 None

    # 頻率名稱暫時設定為 '單次' 或其他預設值，因為這是用藥記錄，不是長期提醒
    frequency_name = get_frequency_name('單次') # 假設有一個 '單次' 頻率
    if not frequency_name:
        logging.error("Frequency '單次' not found in frequency_code table.")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="系統配置錯誤：找不到預設頻率。"))
        clear_temp_state(user_id)
        return

    source_detail = "手動輸入" # 或 "OCR"
    days = 1 # 對於單次記錄，天數設為1

    try:
        # 添加用藥記錄到資料庫
        # medication_record 表中的 dosage_unit 欄位
        add_medication_record(
            recorder_id=user_id,
            member=member,
            drug_id=drug_id,
            frequency_name=frequency_name,
            source_detail=source_detail,
            dose_quantity=dose_quantity,
            dosage_unit=dosage_unit, # 傳遞解析出的 dosage_unit
            days=days,
        )

        # 詢問是否繼續新增其他藥品
        set_temp_state(user_id, {"state": "AWAITING_ADDITIONAL_DRUGS_CHOICE", "member": member})
        message = TextSendMessage(
            text=f"已成功記錄「{member}」在 {record_datetime.strftime('%Y年%m月%d日 %H點%M分')} 服用「{medicine_name} {dosage}」。\n\n是否需要繼續新增其他藥品記錄？",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="是", text="是")),
                QuickReplyButton(action=MessageAction(label="否", text="否"))
            ])
        )
        line_bot_api.reply_message(reply_token, message)

    except Exception as e:
        logging.error(f"Error adding medication record: {e}")
        line_bot_api.reply_message(reply_token, TextSendMessage(text="記錄用藥資訊時發生錯誤，請稍後再試。"))
    finally:
        # 不需要在這裡清空狀態，因為可能還會繼續新增其他藥品
        pass


# ------------------------------------------------------------
# 處理 Postback 事件 (Existing code - modified to include new actions)
# ------------------------------------------------------------
def handle_postback(event, line_bot_api, user_states):
    reply_token = event.reply_token
    line_user_id = event.source.user_id
    postback_data = event.postback.data
    params = {k: v[0] for k, v in parse_qs(postback_data).items()}
    action = params.get("action")
    context = params.get("context") # Get the context
    current_state_info = get_temp_state(line_user_id) # Using get_temp_state from models

    if action == "select_patient_for_reminder":
        member = params.get('member')
        if member:
            if context == "query_reminder":
                # User selected patient for query
                _display_medication_reminders(reply_token, line_bot_api, line_user_id, member)
            elif context == "add_reminder": # Or if context is None, default to add_reminder
                # User selected patient for adding a reminder (OCR or manual)
                set_temp_state(line_user_id, {"state": "AWAITING_MED_SCAN_OR_INPUT", "member": member})
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"已選擇用藥對象為「{member}」。請上傳藥單照片或手動輸入藥品資訊。",
                    quick_reply=QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="手動輸入藥品", text="手動輸入藥品")),
                        QuickReplyButton(action=MessageAction(label="藥袋辨識", text="藥袋辨識"))
                    ])
                ))
            else: # Fallback for unclear context, perhaps a new scenario or old state
                # Default to the "add reminder" flow if context is ambiguous or not provided
                set_temp_state(line_user_id, {"state": "AWAITING_MED_SCAN_OR_INPUT", "member": member})
                line_bot_api.reply_message(reply_token, TextSendMessage(
                    text=f"已選擇用藥對象為「{member}」。請上傳藥單照片或手動輸入藥品資訊。",
                    quick_reply=QuickReply(items=[
                        QuickReplyButton(action=MessageAction(label="手動輸入藥品", text="手動輸入藥品")),
                        QuickReplyButton(action=MessageAction(label="藥袋辨識", text="藥袋辨識"))
                    ])
                ))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="請選擇一個用藥對象。"))
    elif action == "delete_single_reminder":
        member = params.get("member")
        frequency_name = params.get("frequency_name")

        if not member or not frequency_name:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ 缺少刪除參數，請重試。"))
            return

        try:
            from models import delete_medication_reminder_time
            success = delete_medication_reminder_time(line_user_id, member, frequency_name)

            if success:
                # ✅ 刪除成功後 ➜ 直接重新顯示提醒畫面
                _display_medication_reminders(reply_token, line_bot_api, line_user_id, member)
            else:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="⚠️ 找不到可刪除的提醒資料。"))
        except Exception as e:
            logging.error(f"刪除提醒失敗：{e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="❌ 刪除提醒時發生錯誤，請稍後再試。"))


    elif action == "select_patient_for_reminder_initial": # This action is from the "用藥管理" menu to initiate patient selection
        line_bot_api.reply_message(reply_token, create_patient_selection_message(line_user_id, context="manage_reminders")) # Modified call
    elif action == "set_frequency":
        set_temp_state(line_user_id, {"state": "AWAITING_FREQUENCY_SELECTION", **current_state_info})
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text="請選擇用藥頻率：",
            quick_reply=create_frequency_quickreply()
        ))
    elif action == "set_frequency_val":
        frequency_val = params.get("val")
        current_state_info["frequency_code"] = frequency_val
        current_state_info["state"] = "AWAITING_DOSAGE"
        set_temp_state(line_user_id, current_state_info)
        # Check if dosage is already parsed from OCR, if so, ask for confirmation
        if current_state_info.get("dosage") and current_state_info["dosage"] != "未設定":
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text=f"建議劑量為：{current_state_info['dosage']}。正確嗎？",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label="正確", data="action=confirm_dosage_correct")),
                    QuickReplyButton(action=PostbackAction(label="修改劑量", data="action=set_dosage"))
                ])
            ))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="請選擇用藥劑量：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label=opt['label'], data=f"action=set_dosage_val&val={opt['data']}")) for opt in DOSAGE_OPTIONS
                ])
            ))
    elif action == "set_dosage":
        set_temp_state(line_user_id, {"state": "AWAITING_DOSAGE", **current_state_info})
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text="請選擇用藥劑量：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=PostbackAction(label=opt['label'], data=f"action=set_dosage_val&val={opt['data']}")) for opt in DOSAGE_OPTIONS
            ])
        ))
    elif action == "confirm_dosage_correct":
        set_temp_state(line_user_id, {"state": "AWAITING_DAYS_INPUT", **current_state_info})
        if current_state_info.get('days'):
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text=f"用藥天數為：{current_state_info['days']}天。正確嗎？",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label="正確", data="action=confirm_days_correct")),
                    QuickReplyButton(action=PostbackAction(label="修改天數", data="action=set_days"))
                ])
            ))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="請輸入用藥天數：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=MessageAction(label="7天", text="7天")),
                    QuickReplyButton(action=MessageAction(label="14天", text="14天")),
                    QuickReplyButton(action=MessageAction(label="28天", text="28天")),
                    QuickReplyButton(action=MessageAction(label="30天", text="30天")),
                    QuickReplyButton(action=MessageAction(label="長期", text="長期")),
                ])
            ))
    elif action == "confirm_ocr_frequency_correct":
        set_temp_state(line_user_id, {"state": "AWAITING_DOSAGE", **current_state_info})
        # Check if dosage is already parsed from OCR, if so, ask for confirmation
        if current_state_info.get("dosage") and current_state_info["dosage"] != "未設定":
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text=f"建議劑量為：{current_state_info['dosage']}。正確嗎？",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label="正確", data="action=confirm_dosage_correct")),
                    QuickReplyButton(action=PostbackAction(label="修改劑量", data="action=set_dosage"))
                ])
            ))
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text="請選擇用藥劑量：",
                quick_reply=QuickReply(items=[
                    QuickReplyButton(action=PostbackAction(label=opt['label'], data=f"action=set_dosage_val&val={opt['data']}")) for opt in DOSAGE_OPTIONS
                ])
            ))
    elif action == "set_dosage_val":
        dosage_val = params.get("val")
        current_state_info["dosage"] = dosage_val
        current_state_info["state"] = "AWAITING_DAYS_INPUT"
        set_temp_state(line_user_id, current_state_info)
        # Proceed to ask for days
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text="請輸入用藥天數：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="7天", text="7天")),
                QuickReplyButton(action=MessageAction(label="14天", text="14天")),
                QuickReplyButton(action=MessageAction(label="28天", text="28天")),
                QuickReplyButton(action=MessageAction(label="30天", text="30天")),
                QuickReplyButton(action=MessageAction(label="長期", text="長期")),
            ])
        ))
    elif action == "set_days":
        set_temp_state(line_user_id, {"state": "AWAITING_DAYS", **current_state_info})
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text="請輸入用藥天數：",
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=MessageAction(label="7天", text="7天")),
                QuickReplyButton(action=MessageAction(label="14天", text="14天")),
                QuickReplyButton(action=MessageAction(label="28天", text="28天")),
                QuickReplyButton(action=MessageAction(label="30天", text="30天")),
                QuickReplyButton(action=MessageAction(label="長期", text="長期")),
            ])
        ))
    elif action == "confirm_days_correct":
        # Final step for adding medication reminder
        add_medication_reminder_full(line_user_id, current_state_info)
        line_bot_api.reply_message(reply_token, TextSendMessage(text="用藥提醒已成功新增！"))
        clear_temp_state(line_user_id)
    elif action == "set_med_record_time":
        record_time = event.postback.params['time']
        current_state_info["record_time"] = record_time
        set_temp_state(line_user_id, {"state": "CONFIRM_MED_RECORD", **current_state_info})
        # Now, confirm and save record
        member = current_state_info.get("member")
        medicine_name = current_state_info.get("medicine_name")
        dosage = current_state_info.get("dosage")
        record_date = current_state_info.get("record_date") # Assuming record_date is already set

        message_text = (
            f"您確定要記錄「{member}」在 {record_date} {record_time} 服用「{medicine_name}」{dosage} 嗎？"
        )
        line_bot_api.reply_message(reply_token, TextSendMessage(
            text=message_text,
            quick_reply=QuickReply(items=[
                QuickReplyButton(action=PostbackAction(label="確定記錄", data="action=confirm_add_med_record")),
                QuickReplyButton(action=MessageAction(label="取消", text="取消"))
            ])
        ))

    elif action == "confirm_add_med_record":
        member = current_state_info.get("member")
        medicine_name = current_state_info.get("medicine_name")
        dosage = current_state_info.get("dosage")
        record_date = current_state_info.get("record_date")
        record_time = current_state_info.get("record_time")

        if all([member, medicine_name, dosage, record_date, record_time]):
            # Get medicine_id for the drug
            medicine_id = get_medicine_id_by_name(medicine_name)
            if not medicine_id:
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"抱歉，藥品「{medicine_name}」未在資料庫中找到。請手動新增。"))
                clear_temp_state(line_user_id)
                return

            try:
                # Assuming add_medication_record takes patient_id
                # You'll need to get the patient_id from the member name and line_user_id
                patient_id = get_patient_id_by_member_name(line_user_id, member)
                if patient_id:
                    add_medication_record(line_user_id, patient_id, medicine_id, dosage, record_date)
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="用藥記錄已成功新增！"))
                else:
                    line_bot_api.reply_message(reply_token, TextSendMessage(text="找不到該用藥對象的資料。"))
            except Exception as e:
                logging.error(f"Error adding medication record: {e}")
                line_bot_api.reply_message(reply_token, TextSendMessage(text="新增用藥記錄失敗，請稍後再試。"))
            finally:
                clear_temp_state(line_user_id)
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="用藥記錄資訊不完整，請重新開始。"))
            clear_temp_state(line_user_id)

    # Handle reminder management actions
    elif action.startswith("show_reminders_"):
        member = action.split("_")[2] # Extract member from action string
        _display_medication_reminders(reply_token, line_bot_api, line_user_id, member) # Call helper function

    elif action == "delete_reminder_for_member":
        member = params.get('member')
        if not member:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="找不到用藥對象資訊。"))
            return

        conn = get_conn()
        if not conn:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="抱歉，資料庫連線失敗。"))
            return
        try:
            cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT recorder_id FROM users WHERE recorder_id = %s", (line_user_id,))
            user = cursor.fetchone()
            if not user:
                line_bot_api.reply_message(reply_token, TextSendMessage(text="找不到您的使用者資料。"))
                return
            # Using line_user_id directly for patients table now
            # user_id = user['user_id'] # This line is no longer needed to find patient_id
            cursor.execute("SELECT patient_id FROM patients WHERE recorder_id = %s AND member = %s", (line_user_id, member))
            patient = cursor.fetchone()
            if not patient:
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"找不到「{member}」的用藥者資料。"))
                return
            patient_id = patient['patient_id']

            reminders = get_medication_reminders_for_user(patient_id)
            if not reminders:
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"「{member}」目前沒有可刪除的用藥提醒。"))
                return

            items = []
            set_temp_state(line_user_id, {"state": "AWAITING_REMINDER_TO_DELETE", "member": member, "reminders_list": reminders})
            for i, r in enumerate(reminders):
                items.append(
                    QuickReplyButton(
                        action=PostbackAction(
                            label=f"刪除 {r['medicine_name']} ({r['reminder_time']})",
                            data=f"action=confirm_delete_reminder&reminder_index={i}"
                        )
                    )
                )
            line_bot_api.reply_message(reply_token, TextSendMessage(
                text=f"請選擇要刪除「{member}」的哪一個提醒：",
                quick_reply=QuickReply(items=items)
            ))
        except Exception as e:
            logging.error(f"Error preparing delete reminder menu for member {member}: {e}")
            line_bot_api.reply_message(reply_token, TextSendMessage(text="準備刪除提醒失敗，請稍後再試。"))
        finally:
            if conn.is_connected():
                conn.close()

    elif action == "confirm_delete_reminder":
        reminder_index = int(params.get('reminder_index'))
        current_state = get_temp_state(line_user_id)
        reminders_list = current_state.get("reminders_list")
        member = current_state.get("member")

        if reminders_list and 0 <= reminder_index < len(reminders_list):
            reminder_to_delete = reminders_list[reminder_index]
            try:
                delete_medication_reminder_time(reminder_to_delete['reminder_time_id'])
                line_bot_api.reply_message(reply_token, TextSendMessage(text=f"已成功刪除「{member}」的用藥提醒：{reminder_to_delete['medicine_name']} ({reminder_to_delete['reminder_time']})。"))
            except Exception as e:
                logging.error(f"Error deleting reminder: {e}")
                line_bot_api.reply_message(reply_token, TextSendMessage(text="刪除提醒失敗，請稍後再試。"))
            finally:
                clear_temp_state(line_user_id)
        else:
            line_bot_api.reply_message(reply_token, TextSendMessage(text="無效的提醒選擇，請重新操作。"))
            clear_temp_state(line_user_id)


# ... (rest of the existing functions in medication_reminder.py)

# ------------------------------------------------------------
# 處理用藥記錄
# ------------------------------------------------------------
def handle_medication_record_command(reply_token, line_bot_api, user_id):
    # This will now first ask for patient selection
    set_temp_state(user_id, {"state": "AWAITING_MED_RECORD_PATIENT"})
    message = create_patient_selection_message(user_id)
    line_bot_api.reply_message(reply_token, message)

def handle_medication_record_member_selected(reply_token, line_bot_api, user_id, member_name):
    # This function is called after patient selection for medication record
    set_temp_state(user_id, {
        "state": "AWAITING_MED_RECORD_DATE",
        "member": member_name
    })
    message = TextSendMessage(text=f"已選擇用藥對象為「{member_name}」。請選擇用藥日期：",
        quick_reply=QuickReply(items=[
            QuickReplyButton(
                action=DatetimePickerAction(
                    label="選擇日期",
                    data="action=set_med_record_date",
                    mode="date",
                    initial=datetime.date.today().strftime("%Y-%m-%d")
                )
            )
        ])
    )
    line_bot_api.reply_message(reply_token, message)

def handle_medication_record_date_selected(reply_token, line_bot_api, user_id, record_date):
    current_state = get_temp_state(user_id)
    member = current_state.get("member")
    set_temp_state(user_id, {
        "state": "AWAITING_MED_RECORD_MEDICINE_NAME",
        "member": member,
        "record_date": record_date
    })
    line_bot_api.reply_message(reply_token, TextSendMessage(text="請輸入藥品名稱："))

def handle_medication_record_medicine_name_input(reply_token, line_bot_api, user_id, medicine_name):
    current_state = get_temp_state(user_id)
    member = current_state.get("member")
    record_date = current_state.get("record_date")
    set_temp_state(user_id, {
        "state": "AWAITING_MED_RECORD_DOSAGE",
        "member": member,
        "medicine_name": medicine_name,
        "record_date": record_date
    })
    message = TextSendMessage(text="請選擇該次用藥劑量：",
        quick_reply=QuickReply(items=[
            QuickReplyButton(action=PostbackAction(label=opt['label'], data=f"action=set_med_record_dosage&val={opt['data']}")) for opt in DOSAGE_OPTIONS
        ])
    )
    line_bot_api.reply_message(reply_token, message)

def handle_medication_record_dosage_selected(reply_token, line_bot_api, user_id, dosage):
    current_state = get_temp_state(user_id)
    member = current_state.get("member")
    medicine_name = current_state.get("medicine_name")
    record_date = current_state.get("record_date")
    set_temp_state(user_id, {
        "state": "AWAITING_MED_RECORD_TIME",
        "member": member,
        "medicine_name": medicine_name,
        "dosage": dosage,
        "record_date": record_date
    })
    message = TextSendMessage(text="請選擇該次用藥時間：",
        quick_reply=QuickReply(items=[
            QuickReplyButton(
                action=DatetimePickerAction(
                    label="選擇時間",
                    data="action=set_med_record_time",
                    mode="time",
                    initial="08:00" # 提供預設時間
                )
            )
        ])
    )
    line_bot_api.reply_message(reply_token, message)