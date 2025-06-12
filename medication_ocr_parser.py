import re
import datetime

# 假設這是從 medication_schedule.txt 來的 OCR 模擬結果 
# 在實際應用中，這會替換為 OCR 服務返回的真實文字
OCR_SAMPLE_TEXT_FROM_MED_SCHEDULE = """
============================== 用藥提醒列表 ==============================

藥物名稱：妙化錠
用法：每次1顆
時段：每日三次飯前
總量：9.00顆 (共 9 次)
--------------------
藥物名稱：加斯克兒，舒胃錠
用法：每次2顆
時段：每日三次飯前
總量：18.00顆 (共 9 次)
--------------------
藥物名稱：克瀉寧錠
用法：每次1顆
時段：每日三次飯前
總量：9.00顆 (共 9 次)
--------------------
藥物名稱：華興 腹痙寧錠
用法：每次1顆
時段：需要時服用
總量：3.00顆 (共 3 次)
--------------------
藥物名稱：摩舒益多
用法：每次1顆
時段：每日兩次早晚
總量：6.00顆 (共 6 次)
--------------------
藥物名稱：停咳喜液
用法：10.0 CC
時段：睡前
總量：1.00CC (共 3 次)
--------------------

=======================================================================
"""

def call_ocr_service(image_data: bytes) -> str:
    """
    模擬調用 OCR 服務，返回辨識出的原始文字。
    實際應用中，這裡會是與 OCR API 服務互動的程式碼。
    例如，使用 Google Cloud Vision API 或其他服務。
    為了示範，這裡直接返回預設的藥單文字。
    """
    print("DEBUG: 模擬 OCR 服務，請替換為實際的 OCR API 調用。")
    # 這裡直接返回模擬的藥單文字，而不是真的處理 image_data
    return OCR_SAMPLE_TEXT_FROM_MED_SCHEDULE


def parse_medication_order(ocr_text: str) -> list:
    """
    解析 OCR 辨識出的藥單文字，提取藥品名稱、劑量和服用時間/頻率。
    此解析器針對 medication_schedule.txt 的格式進行優化 。

    Args:
        ocr_text: OCR 服務返回的原始文字字串。

    Returns:
        一個列表，每個元素是一個字典，包含 'name', 'dosage', 'frequency_text', 'times'。
        'times' 是從 frequency_text 轉換而來的具體時間列表。
    """
    medications = []

    # 將文字按分隔符 `--------------------` 分割成多個藥品區塊 
    med_blocks = ocr_text.split('--------------------')

    for block in med_blocks:
        block = block.strip()
        if not block:
            continue

        med_info = {'name': '', 'dosage': '', 'frequency_text': '', 'times': []}

        # 提取藥物名稱
        name_match = re.search(r'藥物名稱[：:]\s*(.+)', block)
        if name_match:
            med_info['name'] = name_match.group(1).strip()
            # 移除常見的冗餘信息，例如「，舒胃錠」這種在名稱後面的次要藥品
            if ',' in med_info['name']:
                med_info['name'] = med_info['name'].split(',')[0].strip()

        # 提取用法 (劑量)
        usage_match = re.search(r'用法[：:]\s*(.+)', block)
        if usage_match:
            med_info['dosage'] = usage_match.group(1).strip()
            # 簡單清理劑量描述，例如「每次1顆」只取「1顆」
            if med_info['dosage'].startswith('每次'):
                med_info['dosage'] = med_info['dosage'][2:].strip()

        # 提取時段 (頻率描述)
        frequency_match = re.search(r'時段[：:]\s*(.+)', block)
        if frequency_match:
            med_info['frequency_text'] = frequency_match.group(1).strip()
            # 將頻率描述轉換為具體時間
            med_info['times'] = convert_frequency_to_times(med_info['frequency_text'])

        # 只有當藥品名稱、劑量和頻率/時間都成功提取時才添加
        if med_info['name'] and med_info['dosage'] and (med_info['frequency_text'] or med_info['times']):
            medications.append(med_info)

    return medications


def convert_frequency_to_times(frequency_desc: str) -> list:
    """
    嘗試將頻率描述轉換為常用的具體時間 (HH:MM 格式)。
    這是針對 `medication_schedule.txt` 中的「時段」資訊進行轉換 。
    """
    times = []

    # 首先嘗試提取明確的 HH:MM 時間點
    hhmm_matches = re.findall(r'\b(\d{1,2}:\d{2})\b', frequency_desc)
    if hhmm_matches:
        times.extend(hhmm_matches)

    # 處理頻率描述並映射到常見時間
    if "每日一次" in frequency_desc:
        if not any(t in times for t in ["09:00", "10:00"]): # 避免重複
            times.append("09:00") 
    elif "每日兩次" in frequency_desc or "早晚" in frequency_desc:
        if not any(t in times for t in ["09:00", "21:00"]):
            times.extend(["09:00", "21:00"])
    elif "每日三次" in frequency_desc:
        if not any(t in times for t in ["08:00", "13:00", "18:00"]):
            times.extend(["08:00", "13:00", "18:00"])
    elif "每日四次" in frequency_desc:
        if not any(t in times for t in ["08:00", "12:00", "17:00", "21:00"]):
            times.extend(["08:00", "12:00", "17:00", "21:00"])

    # 處理餐前/餐後/睡前
    if "飯前" in frequency_desc:
        if not any(t in times for t in ["07:30", "12:30", "17:30"]):
            times.extend(["07:30", "12:30", "17:30"])  # 假設三餐飯前時間
    elif "飯後" in frequency_desc:
        if not any(t in times for t in ["09:00", "14:00", "20:00"]):
            times.extend(["09:00", "14:00", "20:00"])  # 假設三餐飯後時間
    elif "早餐後" in frequency_desc:
        if "09:00" not in times: times.append("09:00")
    elif "午餐後" in frequency_desc:
        if "14:00" not in times: times.append("14:00")
    elif "晚餐後" in frequency_desc:
        if "20:00" not in times: times.append("20:00")
    elif "睡前" in frequency_desc:
        if "22:00" not in times: times.append("22:00")
    elif "需要時服用" in frequency_desc or "需要時" in frequency_desc or "急用時" in frequency_desc:
        # 「需要時服用」這種提醒通常不是設定固定時間，可以在提醒訊息中特別說明。
        # 這裡不設定具體時間，或可以設定一個「提醒檢查」的時間
        pass

    # 清理並排序時間，確保格式為 HH:MM
    valid_times = []
    for t in times:
        if re.match(r'^\d{1,2}:\d{2}$', t):
            valid_times.append(t)
    return sorted(list(set(valid_times)))  # 去重並排序
