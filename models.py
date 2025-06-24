from database import get_conn
import random
import string
from datetime import datetime, timedelta
import logging
import json
import re
from linebot.models import TextSendMessage
from urllib.parse import quote
from config import CHANNEL_ACCESS_TOKEN
from linebot import LineBotApi

logging.basicConfig(level=logging.INFO)

# ========================\
# 👤 使用者管理
# ========================\

def get_user_by_recorder_id(recorder_id):
    """
    根據 recorder_id（即 Line User ID）從 users 表中獲取使用者資訊。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute("SELECT * FROM users WHERE recorder_id = %s", (recorder_id,))
        return cursor.fetchone()
    except Exception as e:
        print(f"ERROR: Failed to get user by recorder_id {recorder_id}: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def create_user_if_not_exists(recorder_id):
    """
    如果使用者不存在，則從 LINE API 嘗試取得使用者暱稱，並建立 users 資料。
    """
    from linebot.exceptions import LineBotApiError
    from config import CHANNEL_ACCESS_TOKEN
    from linebot import LineBotApi

    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT recorder_id FROM users WHERE recorder_id=%s", (recorder_id,))
        if not cursor.fetchone():
            user_name = "新用戶"
            try:
                line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)
                profile = line_bot_api.get_profile(recorder_id)
                user_name = profile.display_name
                print(f"✅ 取得使用者暱稱：{user_name}")
            except LineBotApiError as e:
                print(f"⚠️ 無法取得使用者暱稱，使用預設名稱：{e}")

            cursor.execute("INSERT INTO users (recorder_id, user_name) VALUES (%s, %s)", (recorder_id, user_name))
            conn.commit()
            print(f"✅ 已建立使用者資料：{recorder_id}（{user_name}）")
        else:
            print(f"🔁 使用者已存在：{recorder_id}")
    except Exception as e:
        print(f"❌ 建立使用者資料失敗：{e}")
    finally:
        cursor.close()
        conn.close()


# ========================\
# 🧑‍🤝‍🧑 家庭成員管理
# ========================\

def get_all_family_user_ids(recorder_id):
    """
    獲取指定 recorder_id 家庭中所有成員的 Line User ID (即 recorder_id)。
    這包括 recorder_id 本身以及所有被邀請的家庭成員的 recorder_id。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    all_line_ids = set()
    try:
        all_line_ids.add(recorder_id)

        cursor.execute(
            "SELECT recipient_line_id FROM invitation_recipients WHERE inviter_recorder_id = %s",
            (recorder_id,)
        )
        for row in cursor.fetchall():
            all_line_ids.add(row['recipient_line_id'])

        cursor.execute(
            "SELECT inviter_recorder_id FROM invitation_recipients WHERE recipient_line_id = %s",
            (recorder_id,)
        )
        for row in cursor.fetchall():
            all_line_ids.add(row['inviter_recorder_id'])
            sub_cursor = conn.cursor(dictionary=True)
            sub_cursor.execute(
                "SELECT recipient_line_id FROM invitation_recipients WHERE inviter_recorder_id = %s",
                (row['inviter_recorder_id'],)
            )
            for sub_row in sub_cursor.fetchall():
                all_line_ids.add(sub_row['recipient_line_id'])
            sub_cursor.close()
        
        return list(all_line_ids)
    except Exception as e:
        print(f"ERROR: Failed to get all family user IDs for {recorder_id}: {e}")
        return [recorder_id]
    finally:
        cursor.close()
        conn.close()


def add_patient_member(recorder_id, member_name):
    """
    為指定 recorder_id 新增家庭成員。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "INSERT INTO patients (recorder_id, member) VALUES (%s, %s)",
            (recorder_id, member_name)
        )
        conn.commit()
        print(f"DEBUG: Added patient member '{member_name}' for recorder_id {recorder_id}.")
        return True
    except Exception as e:
        print(f"ERROR: Failed to add patient member '{member_name}' for {recorder_id}: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

def get_family_members(recorder_id):
    """
    獲取指定 recorder_id 的所有家庭成員。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute(
            "SELECT member FROM patients WHERE recorder_id = %s",
            (recorder_id,)
        )
        members = cursor.fetchall()
        print(f"DEBUG: Retrieved {len(members)} family members for recorder_id {recorder_id}.")
        return [m['member'] for m in members]
    except Exception as e:
        print(f"ERROR: Failed to get family members for {recorder_id}: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

# ========================\
# 🔗 邀請碼與家人綁定
# ========================\

def generate_invite_code(elder_user_id, expire_minutes=60):
    now = datetime.now()
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    expires_at = now + timedelta(minutes=expire_minutes)

    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO invite_codes (code, inviter_recorder_id, expires_at)
            VALUES (%s, %s, %s)
        """, (code, elder_user_id, expires_at))
        conn.commit()
        return code, expires_at


def bind_family(invite_code, recipient_line_id):
    """
    使用邀請碼綁定家庭關係，並通知邀請人。
    """
    create_user_if_not_exists(recipient_line_id)
    line_bot_api = LineBotApi(CHANNEL_ACCESS_TOKEN)

    with get_conn() as conn:
        cursor = conn.cursor(dictionary=True, buffered=True)

        # 查詢邀請碼
        cursor.execute("SELECT * FROM invite_codes WHERE code = %s", (invite_code,))
        code_row = cursor.fetchone()

        if not code_row:
            return False, None  # 無此邀請碼

        if code_row['used'] or code_row['expires_at'] < datetime.now():
            return False, None  # 已使用或已過期

        inviter_id = code_row['inviter_recorder_id']

        # 避免重複綁定
        cursor.execute("""
            SELECT * FROM invitation_recipients
            WHERE recorder_id = %s AND recipient_line_id = %s
        """, (inviter_id, recipient_line_id))
        if cursor.fetchone():
            return True, inviter_id  # 已經綁定過

        # 取得被邀請人名稱
        cursor.execute("SELECT user_name FROM users WHERE recorder_id = %s", (recipient_line_id,))
        row = cursor.fetchone()
        recipient_name = row['user_name'] if row else "家人"

        # 寫入綁定紀錄（可依需求補 relation_type、recipient_name）
        cursor.execute("""
            INSERT INTO invitation_recipients (recorder_id, recipient_line_id, recipient_name, relation_type)
            VALUES (%s, %s, %s, %s)
        """, (inviter_id, recipient_line_id, recipient_name, '家人'))

        # 更新邀請碼為已使用
        cursor.execute("""
            UPDATE invite_codes 
            SET used = TRUE, bound_at = NOW(), recipient_line_id = %s
            WHERE id = %s
        """, (recipient_line_id, code_row['id']))

        conn.commit()

        # ✅ 通知邀請人
        try:
            line_bot_api.push_message(inviter_id, TextSendMessage(
                text=f"📬 您邀請的 {recipient_name} 已成功綁定，將接收您的用藥提醒。"
            ))
        except Exception as e:
            print(f"⚠️ 發送綁定通知失敗: {e}")

        return True, inviter_id



def get_family_bindings(line_user_id):
    conn = get_conn()
    result = []
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("""
            SELECT '邀請他人' AS role, r.recipient_line_id AS user_id, u.user_name
            FROM invitation_recipients r
            JOIN users u ON r.recipient_line_id = u.recorder_id
            WHERE r.recorder_id = %s

            UNION

            SELECT '被邀請人' AS role, r.recorder_id AS user_id, u.user_name
            FROM invitation_recipients r
            JOIN users u ON r.recorder_id = u.recorder_id
            WHERE r.recipient_line_id = %s
        """, (line_user_id, line_user_id))
        result = cursor.fetchall()
    except Exception as e:
        print(f"ERROR: get_family_bindings 查詢失敗: {e}")
    finally:
        conn.close()
    return result


def unbind_family(line_user_id, target_user_id):
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            DELETE FROM invitation_recipients
            WHERE (recorder_id = %s AND recipient_line_id = %s)
               OR (recorder_id = %s AND recipient_line_id = %s)
        """, (line_user_id, target_user_id, target_user_id, line_user_id))
        conn.commit()
        return cursor.rowcount > 0
    except Exception as e:
        print(f"ERROR: unbind_family 解除失敗: {e}")
        return False
    finally:
        cursor.close()
        conn.close()


# ========================\
# 💊 用藥提醒設定 (主要的修改部分)
# ========================\

def get_suggested_times_by_frequency(frequency_code):
    """
    從 suggested_dosage_time 表中獲取指定頻率的建議時間。
    返回時間字符串列表 (例如 ['08:00', '12:00'])。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    times = []
    try:
        cursor.execute(
            """
            SELECT time_slot_1, time_slot_2, time_slot_3, time_slot_4
            FROM suggested_dosage_time
            WHERE frequency_code = %s
            """,
            (frequency_code,)
        )
        result = cursor.fetchone()
        if result:
            for i in range(1, 5): # 遍歷 time_slot_1 到 time_slot_4
                time_key = f'time_slot_{i}'
                if result[time_key]:
                    times.append(result[time_key].strftime('%H:%M'))
        return times
    except Exception as e:
        print(f"ERROR: Failed to get suggested times for frequency {frequency_code}: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_frequency_name(frequency_code):
    """
    根據 frequency_code 從資料庫取得對應的 frequency_name。
    如果查無資料則回傳 None。
    """
    conn = get_conn()
    if not conn:
        print("ERROR: 無法連接資料庫")
        return None

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT frequency_name FROM frequency_code WHERE frequency_code = %s", (frequency_code,))
            result = cursor.fetchone()
            if result:
                return result[0]
            else:
                print(f"WARNING: 查無對應的 frequency_code: {frequency_code}")
                return None
    except Exception as e:
        print(f"ERROR: 查詢 frequency_name 發生錯誤（code={frequency_code}）: {e}")
        return None
    finally:
        conn.close()

def get_frequency_code(frequency_name):
    """
    根據 frequency_name 從資料庫取得對應的 frequency_code。
    如果查無資料則回傳 None。
    """
    conn = get_conn()
    if not conn:
        print("ERROR: 無法連接資料庫")
        return None

    try:
        with conn.cursor() as cursor:
            cursor.execute("SELECT frequency_code FROM frequency_code WHERE frequency_name = %s", (frequency_name,))
            result = cursor.fetchone()
            if result:
                return result[0]
            else:
                print(f"WARNING: 查無對應的 frequency_name: {frequency_name}")
                return None
    except Exception as e:
        print(f"ERROR: 查詢 frequency_code 發生錯誤（name={frequency_name}）: {e}")
        return None
    finally:
        conn.close()

def get_all_frequency_options():
    """
    從 frequency_code 表中取得所有 frequency_code + frequency_name 對應
    :return: List of (code, name)
    """
    conn = get_conn()
    if not conn:
        return []
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT frequency_code, frequency_name FROM frequency_code")
        results = cursor.fetchall()
        return [(row[0], row[1]) for row in results]
    except Exception as e:
        print(f"❗ get_all_frequency_options error: {e}")
        return []
    finally:
        conn.close()

def get_times_per_day_by_code(frequency_code):
    """
    根據 frequency_code 從資料庫查詢 times_per_day。
    若查不到則預設回傳 4。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT times_per_day FROM frequency_code WHERE frequency_code = %s",
            (frequency_code,)
        )
        result = cursor.fetchone()
        if result:
            return int(result[0])  # 把 float 轉為 int
        else:
            return 4  # 預設最大次數
    except Exception as e:
        print(f"ERROR: get_times_per_day_by_code({frequency_code}) 發生錯誤: {e}")
        return 4
    finally:
        cursor.close()
        conn.close()

def get_frequency_name_by_code(frequency_code):
    """
    根據 frequency_code 查詢中文名稱，例如 QD → 一日一次
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            "SELECT frequency_name FROM frequency_code WHERE frequency_code = %s",
            (frequency_code,)
        )
        result = cursor.fetchone()
        return result[0] if result else frequency_code
    except Exception as e:
        print(f"ERROR: 查詢頻率名稱失敗 ({frequency_code}): {e}")
        return frequency_code
    finally:
        cursor.close()
        conn.close()

def add_medication_reminder_full(recorder_id, member, medicine_name, frequency_code, dosage, days, times):
    import re
    logging.info(f"DEBUG: add_medication_reminder_full called with recorder_id={recorder_id}, member={member}, medicine_name={medicine_name}, frequency_code={frequency_code}, dosage={dosage}, days={days}, times={times}")
    conn = get_conn()
    if not conn:
        logging.error("Failed to connect to database in add_medication_reminder_full.")
        raise Exception("Database connection failed.")
    cursor = conn.cursor(buffered=True)  # ✅ 避免 unread result error

    try:
        # 取得中文頻率名稱
        frequency_name = get_frequency_name(frequency_code)
        if not frequency_name:
            raise ValueError(f"❌ 無法從 frequency_code 查到 frequency_name（傳入: {frequency_code}）")

        # 解析劑量與單位
        parsed_dose_quantity = ""
        dosage_unit = ""
        if dosage:
            match = re.match(r"(\d+\.?\d*)\s*([a-zA-Z%毫升錠顆包個]*)", dosage)
            if match:
                parsed_dose_quantity = match.group(1).strip()
                dosage_unit = match.group(2).strip() or ""
            else:
                parsed_dose_quantity = "1"
        else:
            parsed_dose_quantity = "1"
        dose_quantity = parsed_dose_quantity

        # 嘗試找出現有 mm_id（藥單主表）
        cursor.execute("""
            SELECT mm_id FROM medication_main 
            WHERE recorder_id = %s AND member = %s
        """, (recorder_id, member))
        result = cursor.fetchone()
        if result:
            current_mm_id = result[0]
        else:
            cursor.execute("""
                INSERT INTO medication_main (recorder_id, member, clinic_name, visit_date, doctor_name)
                VALUES (%s, %s, NULL, CURDATE(), NULL)
            """, (recorder_id, member))
            current_mm_id = cursor.lastrowid

        # 新增藥品記錄
        source_detail = "LineBot"
        cursor.execute("""
            INSERT INTO medication_record (
                mm_id, recorder_id, member, drug_name_zh,
                frequency_count_code, source_detail, dose_quantity, days
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
        """, (
            current_mm_id, recorder_id, member, medicine_name,
            frequency_code, source_detail, dose_quantity, days
        ))

        # 準備 reminder_time 時段
        all_time_slots = [None] * 4
        for i, t in enumerate(times[:4]):
            all_time_slots[i] = t

        total_doses_per_day = {
            "QD": 1, "BID": 2, "TID": 3, "QID": 4
        }.get(frequency_code, len(times))

        # 查是否已經有對應 reminder_time
        cursor.execute("""
            SELECT COUNT(*) FROM reminder_time 
            WHERE recorder_id = %s AND member = %s AND frequency_name = %s
        """, (recorder_id, member, frequency_name))
        exists = cursor.fetchone()[0] > 0

        if exists:
            cursor.execute("""
                UPDATE reminder_time
                SET time_slot_1 = %s, time_slot_2 = %s, time_slot_3 = %s, time_slot_4 = %s,
                    total_doses_per_day = %s, updated_at = CURRENT_TIMESTAMP
                WHERE recorder_id = %s AND member = %s AND frequency_name = %s
            """, (
                *all_time_slots, total_doses_per_day,
                recorder_id, member, frequency_name
            ))
        else:
            cursor.execute("""
                INSERT INTO reminder_time (
                    recorder_id, member, frequency_name,
                    time_slot_1, time_slot_2, time_slot_3, time_slot_4,
                    total_doses_per_day
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
            """, (
                recorder_id, member, frequency_name,
                *all_time_slots, total_doses_per_day
            ))

        conn.commit()
        logging.info(f"✅ Medication reminder for {medicine_name} added successfully.")

    except Exception as e:
        conn.rollback()
        logging.error(f"ERROR: Failed to add medication reminder full: {e}")
        raise
    finally:
        if conn and conn.is_connected():
            cursor.close()
            conn.close()

def update_medication_reminder_times(recorder_id, member, frequency_code, new_times):
    """
    更新 reminder_time 表中指定用戶與用藥對象的時間欄位。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        frequency_name = get_frequency_name(frequency_code)
        time_slots = [None] * 4
        for i, t in enumerate(new_times[:4]):
            time_slots[i] = t

        total_doses = len([t for t in time_slots if t])

        cursor.execute("""
            UPDATE reminder_time
            SET time_slot_1 = %s, time_slot_2 = %s, time_slot_3 = %s, time_slot_4 = %s,
                total_doses_per_day = %s, updated_at = CURRENT_TIMESTAMP
            WHERE recorder_id = %s AND member = %s AND frequency_name = %s
        """, (
            *time_slots, total_doses,
            recorder_id, member, frequency_name
        ))
        conn.commit()
        print(f"✅ 提醒時間更新成功：{recorder_id} - {member} - {frequency_name}")
    except Exception as e:
        print(f"❌ 更新提醒時間失敗：{e}")
        conn.rollback()
    finally:
        cursor.close()
        conn.close()



def get_reminder_times_for_user(recorder_id, member):
    """
    從 reminder_time 表中取得用藥提醒時間資訊。
    """
    conn = get_conn()
    if not conn:
        return []
    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        cursor.execute("""
            SELECT frequency_name, time_slot_1, time_slot_2, time_slot_3, time_slot_4
            FROM reminder_time
            WHERE recorder_id = %s AND member = %s
        """, (recorder_id, member))
        return cursor.fetchall()
    except Exception as e:
        logging.error(f"Error fetching reminder times: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------------
# 刪除用藥提醒時間
# ------------------------------------------------------------
def delete_medication_reminder_time(recorder_id, member, frequency_name, time_slot_to_delete=None):
    """
    刪除 reminder_time 的指定時間欄位或整筆資料。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        if time_slot_to_delete:
            # 抓出 reminder_time 記錄
            cursor.execute("""
                SELECT time_slot_1, time_slot_2, time_slot_3, time_slot_4
                FROM reminder_time
                WHERE recorder_id = %s AND member = %s AND frequency_name = %s
            """, (recorder_id, member, frequency_name))
            current_slots = cursor.fetchone()

            if not current_slots:
                print(f"⚠️ 找不到提醒記錄：{recorder_id} - {member} - {frequency_name}")
                return False

            # 比對格式：轉為 H:M 做比對
            updated_slots = []
            for i in range(1, 5):
                slot = current_slots.get(f'time_slot_{i}')
                if slot and slot.strftime('%H:%M') != time_slot_to_delete:
                    updated_slots.append(slot)

            if not updated_slots:
                # 若已刪除所有時間 ➜ 整筆 reminder_time 也刪掉
                cursor.execute("""
                    DELETE FROM reminder_time
                    WHERE recorder_id = %s AND member = %s AND frequency_name = %s
                """, (recorder_id, member, frequency_name))
                conn.commit()
                print(f"🗑️ 已刪除整筆 reminder_time：{recorder_id}-{member}-{frequency_name}")
                return True
            else:
                # 更新剩餘欄位
                sql_update = """
                    UPDATE reminder_time
                    SET time_slot_1 = %s, time_slot_2 = %s, time_slot_3 = %s, time_slot_4 = %s,
                        total_doses_per_day = %s, updated_at = CURRENT_TIMESTAMP
                    WHERE recorder_id = %s AND member = %s AND frequency_name = %s
                """
                slot_values = [None] * 4
                for i, slot in enumerate(updated_slots):
                    slot_values[i] = slot
                cursor.execute(sql_update, (
                    *slot_values,
                    len(updated_slots),
                    recorder_id, member, frequency_name
                ))
                conn.commit()
                print(f"✅ 刪除時間 {time_slot_to_delete} 成功。剩餘：{[t.strftime('%H:%M') for t in updated_slots]}")
                return True
        else:
            # 沒有指定單一時間，刪整筆資料
            cursor.execute("""
                DELETE FROM reminder_time
                WHERE recorder_id = %s AND member = %s AND frequency_name = %s
            """, (recorder_id, member, frequency_name))
            conn.commit()
            print(f"🗑️ 刪除整筆提醒成功：{recorder_id} - {member} - {frequency_name}")
            return cursor.rowcount > 0
    except Exception as e:
        print(f"❌ 刪除 reminder_time 時發生錯誤：{e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()


# ------------------------------------------------------------
# 查詢用藥提醒
# ------------------------------------------------------------
def get_medication_reminders_for_user(line_user_id, member): # 增加 member 參數
    """
    獲取指定 Line 用戶和成員的所有用藥提醒。
    從 medication_record 中獲取藥品名稱，並透過 drug_name_zh 連接 drug_info。
    """
    conn = get_conn()
    if not conn:
        logging.error("Failed to connect to database for get_medication_reminders_for_user.")
        return []

    try:
        cursor = conn.cursor(dictionary=True, buffered=True)
        query = """
        SELECT
            p.member,
            mr.drug_name_zh AS medicine_name,
            mr.dose_quantity,
            mr.dosage_unit,
            rt.frequency_name,
            rt.time_slot_1,
            rt.time_slot_2,
            rt.time_slot_3,
            rt.time_slot_4,
            rt.total_doses_per_day
        FROM
            patients p
        JOIN
            medication_record mr ON p.recorder_id = mr.recorder_id AND p.member = mr.member
        JOIN
            reminder_time rt ON p.recorder_id = rt.recorder_id AND p.member = rt.member
        WHERE
            p.recorder_id = %s AND p.member = %s -- 增加篩選條件：member
        ORDER BY
            p.member, rt.frequency_name;
        """
        cursor.execute(query, (line_user_id, member)) # 傳入 line_user_id 和 member
        return cursor.fetchall()
    except Exception as e:
        logging.error(f"ERROR: Failed to get medication reminders for user {line_user_id} and member {member}: {e}")
        return []
    finally:
        cursor.close()
        if conn and conn.is_connected():
            conn.close()

# ========================\
# 藥品資訊
# ========================\

def get_medicine_list():
    """
    從 drug_info 表中獲取所有藥品名稱。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute("SELECT drug_name_zh FROM drug_info ORDER BY drug_name_zh")
        medicines = cursor.fetchall()
        return [m['drug_name_zh'] for m in medicines]
    except Exception as e:
        print(f"ERROR: Failed to get medicine list: {e}")
        return []
    finally:
        cursor.close()
        conn.close()

def get_medicine_id_by_name(medicine_name):
    """
    根據藥品中文名稱獲取 drug_id。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute("SELECT drug_id FROM drug_info WHERE drug_name_zh = %s", (medicine_name,))
        result = cursor.fetchone()
        return result['drug_id'] if result else None
    except Exception as e:
        print(f"ERROR: Failed to get medicine ID for '{medicine_name}': {e}")
        return None
    finally:
        cursor.close()
        conn.close()

# ========================\
# ⏳ 暫存狀態處理
# ========================\

def set_temp_state(recorder_id, state_data):
    """
    將指定 recorder_id 的暫存狀態儲存到 user_temp_state 表中。
    """
    conn = get_conn()
    cursor = conn.cursor()
    
    state_data_json = json.dumps(state_data) 
    
    try:
        cursor.execute(
            "INSERT INTO user_temp_state (recorder_id, state_data) VALUES (%s, %s) "
            "ON DUPLICATE KEY UPDATE state_data = %s",
            (recorder_id, state_data_json, state_data_json)
        )
        conn.commit()
        print(f"DEBUG: Successfully set temp state for {recorder_id}.")
    except Exception as e:
        print(f"ERROR: Failed to set temp state for {recorder_id}: {e}")
    finally:
        cursor.close()
        conn.close()

def get_temp_state(recorder_id):
    """
    從 user_temp_state 表中獲取指定 recorder_id 的暫存狀態。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True, buffered=True)
    try:
        cursor.execute("SELECT state_data FROM user_temp_state WHERE recorder_id = %s", (recorder_id,))
        result = cursor.fetchone()
        
        if result:
            state_data = json.loads(result['state_data'])
            print(f"DEBUG: Retrieved temp state for {recorder_id}: {state_data}")
            return state_data
        else:
            print(f"DEBUG: No temp state found for {recorder_id}.")
            return None
    except Exception as e:
        print(f"ERROR: Failed to get temp state for {recorder_id}: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def clear_temp_state(recorder_id):
    """
    清除指定 recorder_id 的暫存狀態。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM user_temp_state WHERE recorder_id = %s", (recorder_id,))
        conn.commit()
        print(f"DEBUG: Cleared temp state for {recorder_id}.")
    except Exception as e:
        print(f"ERROR: Failed to clear temp state for {recorder_id}: {e}")
    finally:
        cursor.close()
        conn.close()

# ========================\
# 📝 用藥記錄
# ========================\

def add_medication_record(recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days):
    """
    新增一筆用藥記錄到 medication_record 表。
    現在使用 drug_name_zh 而非 drug_id。
    """
    conn = get_conn()
    if not conn:
        logging.error("Failed to connect to database for add_medication_record.")
        raise Exception("Database connection failed.")

    try:
        cursor = conn.cursor()
        current_mm_id = None
        cursor.execute(
            "SELECT mm_id FROM medication_main WHERE recorder_id = %s AND member = %s ORDER BY visit_date DESC LIMIT 1",
            (recorder_id, member)
        )
        main_record = cursor.fetchone()
        if main_record:
            current_mm_id = main_record[0]
        else:
            # 如果沒有，則創建一個新的 medication_main 記錄
            # 假設有一個預設的 clinic_id 和 doctor_name
            clinic_id = 1 # 假設預設診所ID為1
            cursor.execute(
            """
            INSERT INTO medication_record (mm_id, recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (current_mm_id, recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days)
        )
            current_mm_id = cursor.lastrowid
            logging.info(f"DEBUG: Created a default medication_main record with mm_id: {current_mm_id}")


        if not current_mm_id:
            raise Exception("Failed to get or create mm_id for medication_record.")

        # 3. 插入 medication_record
        # 由於 medication_record 沒有 PRIMARY KEY，每次呼叫都會新增一條。
        # 如果需要更新，則需要在這裡添加 SELECT 和 UPDATE 邏輯。
        cursor.execute(
            """
            INSERT INTO medication_record (mm_id, recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (current_mm_id, recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days)
        )
        conn.commit()
        logging.info(f"DEBUG: Added medication record for {member} with {drug_name_zh}")

    except Exception as e:
        conn.rollback()
        logging.error(f"ERROR: Failed to add medication record: {e}")
        raise
    finally:
        cursor.close()
        if conn and conn.is_connected():
            conn.close()