# models.py (完整內容)

from database import get_conn
import random
import string
from datetime import datetime, timedelta
import logging
import json

logging.basicConfig(level=logging.INFO)

# ========================\
# 👤 使用者管理
# ========================\

def get_user_by_recorder_id(recorder_id):
    """
    根據 recorder_id（即 Line User ID）從 users 表中獲取使用者資訊。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
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
    如果使用者不存在，則在 users 表中創建新的使用者記錄。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute("SELECT recorder_id FROM users WHERE recorder_id=%s", (recorder_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (recorder_id, user_name) VALUES (%s, %s)", (recorder_id, '新用戶'))
            conn.commit()
            print(f"DEBUG: Created new user with recorder_id: {recorder_id}")
        else:
            print(f"DEBUG: User with recorder_id: {recorder_id} already exists.")
    except Exception as e:
        print(f"ERROR: Failed to create user if not exists for {recorder_id}: {e}")
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
    cursor = conn.cursor(dictionary=True)
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
    cursor = conn.cursor(dictionary=True)
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

def generate_invite_code(inviter_recorder_id):
    """
    為邀請者生成一個唯一的邀請碼。
    """
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    conn = get_conn()
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO invite_codes (inviter_recorder_id, code)
            VALUES (%s, %s)
            """, (inviter_recorder_id, code))
        conn.commit()
        print(f"DEBUG: Generated invite code {code} for inviter {inviter_recorder_id}")
        return code
    except Exception as e:
        print(f"ERROR: Failed to generate invite code for {inviter_recorder_id}: {e}")
        return None
    finally:
        cursor.close()
        conn.close()

def bind_family(invite_code, recipient_recorder_id, recipient_name=None, relation_type='未定義'):
    """
    將接收者與邀請碼綁定，建立家庭關係。
    返回 (是否成功, 邀請者的 recorder_id 或 None)。
    """
    conn = get_conn()
    cursor = conn.cursor()
    inviter_recorder_id = None
    try:
        cursor.execute(
            "SELECT inviter_recorder_id FROM invite_codes WHERE code = %s AND bound_at IS NULL",
            (invite_code,)
        )
        invite_info = cursor.fetchone()

        if not invite_info:
            print(f"INFO: Invalid or already used invite code: {invite_code}")
            return False, None

        inviter_recorder_id = invite_info[0]

        cursor.execute(
            "SELECT COUNT(*) FROM invitation_recipients WHERE inviter_recorder_id = %s AND recipient_line_id = %s",
            (inviter_recorder_id, recipient_recorder_id)
        )
        if cursor.fetchone()[0] > 0:
            print(f"INFO: Family binding already exists between {inviter_recorder_id} and {recipient_recorder_id}.")
            return False, None

        cursor.execute(
            """
            INSERT INTO invitation_recipients (inviter_recorder_id, recipient_line_id, recipient_name, relation_type)
            VALUES (%s, %s, %s, %s)
            """,
            (inviter_recorder_id, recipient_recorder_id, recipient_name, relation_type)
        )

        cursor.execute(
            "UPDATE invite_codes SET bound_at = CURRENT_TIMESTAMP WHERE code = %s",
            (invite_code,)
        )
        conn.commit()
        print(f"DEBUG: Successfully bound family: inviter {inviter_recorder_id} to recipient {recipient_recorder_id}")
        return True, inviter_recorder_id
    except Exception as e:
        conn.rollback()
        print(f"ERROR: Failed to bind family for invite code {invite_code} and recipient {recipient_recorder_id}: {e}")
        return False, None
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
    cursor = conn.cursor(dictionary=True)
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
    根據 frequency_code 獲取 frequency_name。
    """
    conn = get_conn()
    cursor = conn.cursor()
    name = None
    try:
        cursor.execute("SELECT frequency_name FROM frequency_code WHERE frequency_code = %s", (frequency_code,))
        result = cursor.fetchone()
        if result:
            name = result[0]
    except Exception as e:
        print(f"ERROR: Failed to get frequency name for code {frequency_code}: {e}")
    finally:
        cursor.close()
        conn.close()
    return name if name else frequency_code

def add_medication_reminder_full(user_id, member, drug_name, dosage, frequency_type, frequency_code, times):
    """
    將完整的用藥提醒資訊儲存到相關表 (drug_info, medication_record, reminder_time)。
    這個函數假設 dosage 已經是格式化的字符串（例如 "1 錠"）。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        # 1. 確保 drug_info 中有該藥品，並獲取 drug_id
        # 先嘗試獲取現有的 drug_id
        drug_id = get_medicine_id_by_name(drug_name)
        if not drug_id:
            # 如果藥品不存在，則插入一個新的 drug_info 記錄
            # drug_id 是 VARCHAR(15)，需要手動生成一個唯一的
            new_drug_id = f"DRUG_{datetime.now().strftime('%Y%m%d%H%M%S')}_{random.randint(1000, 9999)}"
            cursor.execute(
                "INSERT INTO drug_info (drug_id, drug_name_zh) VALUES (%s, %s)",
                (new_drug_id, drug_name)
            )
            conn.commit()
            drug_id = new_drug_id # 使用新生成的 ID
            print(f"DEBUG: New drug '{drug_name}' added to drug_info with ID {drug_id}.")
        
        # 解析 dosage 為 quantity 和 unit
        try:
            parts = dosage.split(' ')
            dosage_quantity = float(parts[0])
            dosage_unit = parts[1] if len(parts) > 1 else ''
        except ValueError:
            dosage_quantity = dosage # 如果無法解析為數字
            dosage_unit = ''


        # 2. 插入或更新 medication_record
        # medication_record 記錄的是「處方詳情」或「計劃用藥」
        # 這裡的邏輯需要確保 recorder_id, member, drug_id, frequency_name 組合的唯一性
        success_med_record = add_medication_record(
            # mm_id 這裡暫時為 None，讓 add_medication_record 內部處理或創建
            mm_id=None, # 或從 temp_state 獲取
            recorder_id=user_id,
            member=member,
            drug_id=drug_id,
            frequency_name=frequency_type,
            source_detail='提醒設定',
            dose_quantity=dosage_quantity,
            dosage_unit=dosage_unit,
            days=0 # 或其他預設值
        )
        if not success_med_record:
            raise Exception("Failed to add or update medication_record.")


        # 3. 插入或更新 reminder_time
        # reminder_time 的 PRIMARY KEY 是 (recorder_id, member, frequency_name)
        # 所以對同一個 recorder_id, member, frequency_name 的組合，會執行 UPDATE
        
        # 填充 time_slot 欄位
        times_to_insert = [None] * 4
        for i, time_str in enumerate(times):
            if i < 4:
                times_to_insert[i] = time_str
            else:
                print(f"WARN: More than 4 time slots provided for reminder_time. Only first 5 will be saved.")
                break # 超出範圍則跳出

        # 計算 total_doses_per_day
        total_doses_per_day = len(times)

        sql_reminder_time = """
        INSERT INTO reminder_time (
            recorder_id, member, frequency_name,
            time_slot_1, time_slot_2, time_slot_3, time_slot_4, time_slot_5,
            total_doses_per_day
        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
        ON DUPLICATE KEY UPDATE
            time_slot_1 = VALUES(time_slot_1),
            time_slot_2 = VALUES(time_slot_2),
            time_slot_3 = VALUES(time_slot_3),
            time_slot_4 = VALUES(time_slot_4),
            time_slot_5 = VALUES(time_slot_5),
            total_doses_per_day = VALUES(total_doses_per_day),
            updated_at = CURRENT_TIMESTAMP;
        """
        cursor.execute(sql_reminder_time, (
            user_id, member, frequency_type, # 這裡的 frequency_name 是易讀的，與 reminder_time PK 一致
            times_to_insert[0], times_to_insert[1], times_to_insert[2],
            times_to_insert[3], times_to_insert[4],
            total_doses_per_day
        ))
        conn.commit()
        
        print(f"DEBUG: Reminder time added/updated for {user_id}-{member}-{frequency_type} at {times}.")
        return True
    except Exception as e:
        conn.rollback()
        print(f"ERROR: Failed to add medication reminder full: {e}")
        return False
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------------
# 刪除用藥提醒時間
# ------------------------------------------------------------
def delete_medication_reminder_time(recorder_id, member, frequency_name, time_slot_to_delete=None):
    """
    從資料庫中刪除指定用戶、成員、頻率下的一個特定用藥時間點，或整個頻率的提醒。
    注意：目前的 reminder_time 設計，刪除單一時間點較複雜，建議刪除整個頻率。
    """
    conn = get_conn()
    cursor = conn.cursor()
    try:
        if time_slot_to_delete:
            # 獲取當前 reminder_time 記錄
            cursor.execute("""
                SELECT time_slot_1, time_slot_2, time_slot_3, time_slot_4, time_slot_5
                FROM reminder_time
                WHERE recorder_id = %s AND member = %s AND frequency_name = %s
            """, (recorder_id, member, frequency_name))
            current_slots = cursor.fetchone()

            if current_slots:
                # 將結果轉換為列表，並移除要刪除的時間點
                updated_slots = []
                for i in range(1, 6):
                    slot_val = current_slots[f'time_slot_{i}']
                    if slot_val and slot_val.strftime('%H:%M') != time_slot_to_delete:
                        updated_slots.append(slot_val.strftime('%H:%M'))
                
                # 如果沒有時間點了，就刪除整條記錄
                if not updated_slots:
                    cursor.execute("""
                        DELETE FROM reminder_time
                        WHERE recorder_id = %s AND member = %s AND frequency_name = %s
                    """, (recorder_id, member, frequency_name))
                else:
                    # 重新更新時間欄位
                    sql_update_slots = """
                        UPDATE reminder_time
                        SET time_slot_1 = %s, time_slot_2 = %s, time_slot_3 = %s, time_slot_4 = %s, time_slot_5 = %s,
                            total_doses_per_day = %s, updated_at = CURRENT_TIMESTAMP
                        WHERE recorder_id = %s AND member = %s AND frequency_name = %s
                    """
                    new_slots_data = [None] * 5
                    for i, ts in enumerate(updated_slots):
                        if i < 5: new_slots_data[i] = ts
                    
                    cursor.execute(sql_update_slots, (
                        new_slots_data[0], new_slots_data[1], new_slots_data[2], new_slots_data[3], new_slots_data[4],
                        len(updated_slots), recorder_id, member, frequency_name
                    ))
                conn.commit()
                print(f"DEBUG: Deleted time {time_slot_to_delete} for {recorder_id}-{member}-{frequency_name}. Remaining: {updated_slots}")
                return True
            else:
                print(f"WARN: No reminder found for {recorder_id}-{member}-{frequency_name} to delete time {time_slot_to_delete}.")
                return False

        else:
            # 如果沒有指定 time_slot_to_delete，則刪除整個頻率的提醒設定
            cursor.execute("""
                DELETE FROM reminder_time
                WHERE recorder_id = %s AND member = %s AND frequency_name = %s
            """, (recorder_id, member, frequency_name))
            conn.commit()
            print(f"DEBUG: Deleted entire reminder for {recorder_id}-{member}-{frequency_name}.")
            return cursor.rowcount > 0
    except Exception as e:
        print(f"ERROR: Failed to delete medication reminder time: {e}")
        conn.rollback()
        return False
    finally:
        cursor.close()
        conn.close()

# ------------------------------------------------------------
# 查詢用藥提醒
# ------------------------------------------------------------
def get_medication_reminders_for_user(line_user_id):
    """
    獲取指定 Line 用戶的所有用藥提醒 (包括自己和家庭成員)。
    從 medication_record 中獲取藥品名稱，並透過 drug_name_zh 連接 drug_info。
    """
    conn = get_conn()
    if not conn:
        logging.error("Failed to connect to database for get_medication_reminders_for_user.")
        return []

    try:
        cursor = conn.cursor(dictionary=True)
        query = """
        SELECT
            p.member,
            mr.drug_name_zh AS medicine_name, -- 直接從 medication_record 獲取藥品名稱
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
            p.recorder_id = %s -- 查詢當前用戶的用藥者
            -- 如果需要根據 drug_info 獲取額外資訊，可以使用 LEFT JOIN
            -- LEFT JOIN drug_info di ON mr.drug_name_zh = di.drug_name_zh
        ORDER BY
            p.member, rt.frequency_name;
        """
        cursor.execute(query, (line_user_id,))
        return cursor.fetchall()
    except Exception as e:
        logging.error(f"ERROR: Failed to get medication reminders for user {line_user_id}: {e}")
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
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT name_zh FROM drug_info ORDER BY name_zh")
        medicines = cursor.fetchall()
        return [m['name_zh'] for m in medicines]
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
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT drug_id FROM drug_info WHERE name_zh = %s", (medicine_name,))
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
    cursor = conn.cursor(dictionary=True)
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

def add_medication_record(recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days, record_datetime):
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

        # 1. 確保 recorder_id 存在於 users 表中
        # (這裡假設 create_user_if_not_exists 已在前面呼叫確保使用者存在)

        # 2. 獲取或創建 medication_main 的 mm_id
        # 這裡的邏輯需要確保mm_id的生成和使用是合理的
        # 由於 medication_record 表有一個 mm_id FOREIGN KEY，它需要一個有效的 mm_id
        # 如果每個 medication_record 都需要關聯到一個 medication_main，則需要確保這部分邏輯正確
        # 根據 models.py 的 snippet，它有嘗試獲取或創建 mm_id 的邏輯
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
                "INSERT INTO medication_main (recorder_id, member, clinic_id, visit_date, doctor_name) VALUES (%s, %s, %s, %s, %s)",
                (recorder_id, member, clinic_id, datetime.now().date(), '未知醫生')
            )
            conn.commit()
            current_mm_id = cursor.lastrowid
            logging.info(f"DEBUG: Created a default medication_main record with mm_id: {current_mm_id}")


        if not current_mm_id:
            raise Exception("Failed to get or create mm_id for medication_record.")

        # 3. 插入 medication_record
        # 由於 medication_record 沒有 PRIMARY KEY，每次呼叫都會新增一條。
        # 如果需要更新，則需要在這裡添加 SELECT 和 UPDATE 邏輯。
        cursor.execute(
            """
            INSERT INTO medication_record (mm_id, recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days, record_datetime)
            VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
            """,
            (current_mm_id, recorder_id, member, drug_name_zh, frequency_name, source_detail, dose_quantity, dosage_unit, days, record_datetime)
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