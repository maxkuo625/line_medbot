from database import get_conn
import random
import string
from datetime import datetime, timedelta
import logging
import json

logging.basicConfig(level=logging.INFO)

# ========================
# 👤 使用者管理
# ========================

def get_user_by_line_id(line_user_id):
    with get_conn() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM users WHERE line_user_id = %s", (line_user_id,))
        return cursor.fetchone()

def create_user_if_not_exists(line_user_id):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE line_user_id=%s", (line_user_id,))
        if not cursor.fetchone():
            cursor.execute("INSERT INTO users (line_user_id) VALUES (%s)", (line_user_id,))
            conn.commit()

# ========================
# 🧑‍🤝‍🧑 邀請碼與家人綁定
# ========================

def generate_invite_code(elder_user_id):
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO invite_codes (elder_user_id, invite_code)
            VALUES (%s, %s)
        """, (elder_user_id, code))
        conn.commit()
        return code

def bind_family(invite_code, family_user_id):
    with get_conn() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT * FROM invite_codes 
            WHERE invite_code=%s AND used=FALSE
        """, (invite_code,))
        result = cursor.fetchone()
        if not result:
            return False, None

        elder_user_id = result['elder_user_id']

        # 檢查是否已綁定
        cursor.execute("""
            SELECT * FROM family_bindings
            WHERE elder_user_id=%s AND family_user_id=%s
        """, (elder_user_id, family_user_id))
        if cursor.fetchone():
            return True, elder_user_id  # 已綁定，視為成功

        # 建立綁定
        cursor.execute("""
            INSERT INTO family_bindings (elder_user_id, family_user_id) 
            VALUES (%s, %s)
        """, (elder_user_id, family_user_id))

        # 標記邀請碼已使用
        cursor.execute("UPDATE invite_codes SET used=TRUE WHERE id=%s", (result['id'],))
        conn.commit()
        return True, elder_user_id

def unbind_family(elder_user_id, family_user_id):
    with get_conn() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            DELETE FROM family_bindings 
            WHERE elder_user_id=%s AND family_user_id=%s
        """, (elder_user_id, family_user_id))
        conn.commit()

def get_family_members(elder_user_id):
    with get_conn() as conn:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT family_user_id FROM family_bindings
            WHERE elder_user_id = %s
        """, (elder_user_id,))
        return [row['family_user_id'] for row in cursor.fetchall()]

def get_all_family_user_ids(user_id):
    with get_conn() as conn:
        cursor = conn.cursor(dictionary=True)

        # 檢查是否為 elder
        cursor.execute("""
            SELECT family_user_id FROM family_bindings WHERE elder_user_id = %s
        """, (user_id,))
        results = cursor.fetchall()
        if results:
            ids = [user_id] + [row['family_user_id'] for row in results]
            return list(set(ids))

        # 如果是 family，查 elder，再查全體
        cursor.execute("""
            SELECT elder_user_id FROM family_bindings WHERE family_user_id = %s
        """, (user_id,))
        row = cursor.fetchone()
        if row:
            elder_user_id = row['elder_user_id']
            cursor.execute("""
                SELECT family_user_id FROM family_bindings WHERE elder_user_id = %s
            """, (elder_user_id,))
            members = [r['family_user_id'] for r in cursor.fetchall()]
            return list(set([elder_user_id] + members))

        return [user_id]

# ========================
# 💊 藥品與提醒邏輯
# ========================

def get_medicine_list():
    """
    從資料庫中獲取所有藥品列表。
    應該返回一個字典列表，每個字典包含 'id' 和 'name'。
    例如：[{'id': 1, 'name': '止痛藥'}, {'id': 2, 'name': '感冒藥'}]
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True) 
    cursor.execute("SELECT id, name FROM medicines ORDER BY name") # 選擇 id 和 name
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return results

def add_medication_reminder(user_id, medicine_id, time_str, dosage=None, frequency=None):
    """
    加入用藥提醒，儲存 medicine_id, time_str, dosage, frequency。
    """
    conn = get_conn()
    cursor = conn.cursor()
    # 檢查是否已存在相同的提醒（user_id, medicine_id, time_str），以避免重複
    cursor.execute(
         "SELECT 1 FROM user_medication WHERE user_id = %s AND medicine_id = %s AND time_str = %s",
        (user_id, medicine_id, time_str)
    )
    if not cursor.fetchone(): # 如果不存在才新增
        cursor.execute(
            "INSERT INTO user_medication (user_id, medicine_id, time_str, dosage, frequency) VALUES (%s, %s, %s, %s, %s)",
            (user_id, medicine_id, time_str, dosage, frequency)
        )
        conn.commit()
    # 如果已經存在，則更新劑量和頻率
    else:
        cursor.execute(
            "UPDATE user_medication SET dosage = %s, frequency = %s WHERE user_id = %s AND medicine_id = %s AND time_str = %s",
            (dosage, frequency, user_id, medicine_id, time_str)
        )
        conn.commit()

    cursor.close()
    conn.close()

# 修改：查詢時選取 dosage 和 frequency
def get_medication_reminders_for_user(user_id):
    """
    查詢使用者已設定的用藥提醒，包含藥品名稱、時間、劑量和頻率。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT um.time_str, m.name AS medicine_name, m.id AS medicine_id, um.dosage, um.frequency
        FROM user_medication um
        JOIN medicines m ON um.medicine_id = m.id
        WHERE um.user_id = %s
        ORDER BY um.time_str
        """,
        (user_id,)
    )
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    # 返回字典列表，包含 time_str, medicine_name, medicine_id, dosage, frequency
    return results

def delete_medication_reminder(user_id, medicine_id, time_str):
    """
    刪除指定藥品和時間的用藥提醒。
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_medication WHERE user_id = %s AND medicine_id = %s AND time_str = %s",
        (user_id, medicine_id, time_str)
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_medication_reminders_for_user(user_id):
    """
    查詢使用者已設定的用藥提醒，包含藥品名稱和時間。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute(
        """
        SELECT um.time_str, m.name AS medicine_name, m.id AS medicine_id
        FROM user_medication um
        JOIN medicines m ON um.medicine_id = m.id
        WHERE um.user_id = %s
        ORDER BY um.time_str
        """,
        (user_id,)
    )
    results = cursor.fetchall()
    cursor.close()
    conn.close()
    return results # 返回字典列表，包含 time_str, medicine_name, medicine_id

def get_medicine_id_by_name(medicine_name: str):
    """
    根據藥品名稱查詢其在資料庫中的 ID。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute("SELECT id FROM medicines WHERE name = %s", (medicine_name,))
        result = cursor.fetchone()
        return result['id'] if result else None
    except Exception as e:
        print(f"ERROR: Failed to get medicine ID for '{medicine_name}': {e}")
        return None
    finally:
        cursor.close()
        conn.close()


# ========================
# ⏳ 暫存狀態處理
# ========================

def set_temp_state(user_id, state_data):
    """
    將指定 user_id 的暫存狀態儲存到 user_temp_state 表中。
    手動將 Python 字典轉換為 JSON 字串，再傳遞給資料庫。
    """
    conn = get_conn()
    cursor = conn.cursor()
    
    state_data_json = json.dumps(state_data) 
    
    cursor.execute(
        "INSERT INTO user_temp_state (user_id, state_data) VALUES (%s, %s) "
        "ON DUPLICATE KEY UPDATE state_data = %s",
        (user_id, state_data_json, state_data_json) 
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_temp_state(user_id):
    """
    從 user_temp_state 表中獲取指定 user_id 的暫存狀態。
    """
    conn = get_conn()
    cursor = conn.cursor(dictionary=True)
    cursor.execute("SELECT state_data FROM user_temp_state WHERE user_id = %s", (user_id,))
    result = cursor.fetchone()
    cursor.close()
    conn.close()
    if result and result['state_data']:
        return json.loads(result['state_data'])
    return None

def clear_temp_state(user_id):
    """
    清除指定 user_id 的暫存狀態。
    """
    conn = get_conn()
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM user_temp_state WHERE user_id = %s",
        (user_id,)
    )
    conn.commit()
    cursor.close()
    conn.close()