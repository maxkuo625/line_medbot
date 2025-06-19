import re
from typing import List, Dict, Any
# 假設這裡可以引入 models.py 中的函數，例如查詢頻率對應時間的函數
# from models import get_suggested_times_by_frequency_name # ⚠️ 需要在 models.py 中實現此函數
import logging

logging.basicConfig(level=logging.INFO)

# 模擬 OCR 服務
def call_ocr_service(image_data: bytes) -> str:
    """
    模擬對藥袋圖片進行 OCR 辨識的服務。
    在實際應用中，這裡會調用真正的 OCR API (例如 Google Cloud Vision API)。
    目前為了演示，它會返回一個預設的藥袋文字。

    Args:
        image_data (bytes): 藥袋圖片的二進制數據。

    Returns:
        str: OCR 辨識後的原始文字。
    """
    logging.info("DEBUG: 模擬 OCR 服務已調用。")
    # 這裡返回一個模擬的藥袋文字，模擬 OCR 辨識的結果
    # 根據你的流程圖，範例文字如下：
    mock_ocr_result = """
看診日期:114.06.12
本次發藥天數:3日份

藥品名稱 單次劑量 用藥頻率 主要用途 副作用
普拿疼 2 一日三次 止痛
脈優錠 1 飯後早中晚 治療高血壓 嘔吐 頭暈
"""
    return mock_ocr_result.strip()

def convert_frequency_to_times(frequency_text: str) -> List[str]:
    """
    將用藥頻率的文字描述轉換為具體的服藥時間列表 (HH:MM 格式)。
    此函數應更智能地利用預設的頻率代碼和建議時間。
    
    Args:
        frequency_text (str): 用藥頻率的文字描述，例如 "一日三次", "飯後早中晚", "睡前"。

    Returns:
        List[str]: 具體的服藥時間列表，例如 ["08:00", "14:00", "20:00"]。
    """
    frequency_text = frequency_text.strip()
    times = []

    # ⚠️ 這裡需要從資料庫查詢 suggested_dosage_time 表，
    # 根據 frequency_text 找到對應的 frequency_code 和其 time_slot。
    # 假設 models.py 中有一個函數可以查詢這些資訊。

    # 模擬從 suggested_dosage_time 查詢
    # 在實際應用中，這裡會調用 models.py 中的函數，例如：
    # suggested_times_data = get_suggested_times_by_frequency_name(frequency_text)
    # if suggested_times_data:
    #     for i in range(1, 5): # time_slot_1 到 time_slot_4
    #         time_slot = suggested_times_data.get(f'time_slot_{i}')
    #         if time_slot:
    #             times.append(str(time_slot)) # 假設 time_slot 是 datetime.time 對象

    # 臨時的硬編碼映射，最終應該從資料庫獲取
    # 參考您的 suggested_dosage_time 表範例
    if "一日一次" in frequency_text:
        times = ["08:00"]
    elif "一日二次" in frequency_text:
        times = ["08:00", "20:00"]
    elif "一日三次" in frequency_text or "飯後早中晚" in frequency_text:
        times = ["08:30", "12:30", "18:30"] # 飯後時間
    elif "一日四次" in frequency_text:
        times = ["06:00", "12:00", "18:00", "22:00"]
    elif "睡前" in frequency_text or "HS" in frequency_text:
        times = ["22:00"]
    elif "飯前" in frequency_text or "AC" in frequency_text:
        times = ["07:30", "11:30", "17:30"]
    elif "飯後" in frequency_text or "PC" in frequency_text:
        times = ["08:30", "12:30", "18:30"]
    elif "視需要服用" in frequency_text or "PRN" in frequency_text or "需要時" in frequency_text:
        # 對於需要時服用的藥品，不設定固定提醒時間，由應用程式邏輯處理
        return []
    # 可以添加更多頻率的轉換邏輯

    return times

def parse_medication_order(ocr_raw_text: str) -> List[Dict[str, Any]]:
    """
    解析 OCR 辨識出的藥袋原始文字，提取藥品資訊。

    Args:
        ocr_raw_text (str): OCR 辨識後的原始文字。

    Returns:
        List[Dict[str, Any]]: 包含解析出的藥品資訊的列表，每個字典代表一種藥品。
                                格式如：
                                [
                                    {
                                        'name': '普拿疼',
                                        'dosage': '2',
                                        'frequency_text': '一日三次', # 原始頻率文字
                                        'times': ['08:00', '14:00', '20:00'], # 轉換後的具體時間
                                        'purpose': '止痛',
                                        'side_effects': ''
                                    },
                                    {
                                        'name': '脈優錠',
                                        'dosage': '1',
                                        'frequency_text': '飯後早中晚',
                                        'times': ['08:30', '12:30', '18:30'],
                                        'purpose': '治療高血壓',
                                        'side_effects': '嘔吐 頭暈'
                                    }
                                ]
    """
    lines = ocr_raw_text.split('\n')
    parsed_medications = []
    
    # 提取看診日期和發藥天數 (如果需要儲存這些資訊)
    consultation_date = ""
    days_supply = ""
    for line in lines:
        if "看診日期" in line:
            match = re.search(r"看診日期:(\d{3}\.\d{2}\.\d{2})", line)
            if match:
                consultation_date = match.group(1)
        elif "本次發藥天數" in line:
            match = re.search(r"本次發藥天數:(\d+)日份", line)
            if match:
                days_supply = match.group(1)

    # 找到藥品列表的起始行
    start_parsing = False
    for line in lines:
        # 尋找包含關鍵詞的標題行，例如 "藥品名稱 單次劑量 用藥頻率 主要用途 副作用"
        if "藥品名稱" in line and "單次劑量" in line and "用藥頻率" in line:
            start_parsing = True
            continue

        if start_parsing and line.strip(): # 確保不是空行
            # 嘗試使用正則表達式解析藥品資訊
            # 假設字段由至少一個空格分隔
            # (\S+) 匹配非空白字符，用於藥品名稱
            # (\d+) 匹配一個或多個數字，用於劑量
            # (\S+) 匹配非空白字符，用於用藥頻率 (例如 "一日三次", "飯後早中晚")
            # ([^\s]+(?: [^\s]+)*) 匹配主要用途，可以包含多個詞語，中間有空格
            # (?:\s+(.*))? 可選地匹配副作用，從一個或多個空格開始到行尾
            match = re.match(r"(\S+)\s+(\d+)\s+(\S+)\s+([^\s]+(?: [^\s]+)*)(?:\s+(.*))?", line.strip())

            if match:
                name = match.group(1).strip()
                dosage = match.group(2).strip()
                frequency_text = match.group(3).strip()
                purpose = match.group(4).strip()
                side_effects = match.group(5).strip() if match.group(5) else ""

                # 轉換頻率文字為具體時間點
                times = convert_frequency_to_times(frequency_text)

                parsed_medications.append({
                    'name': name,
                    'dosage': dosage,
                    'frequency_text': frequency_text,
                    'times': times,
                    'purpose': purpose,
                    'side_effects': side_effects
                })
            else:
                logging.warning(f"WARN: 無法解析藥品信息行: {line.strip()}")
                # 如果無法解析，可以選擇跳過或記錄錯誤
                pass

    return parsed_medications

