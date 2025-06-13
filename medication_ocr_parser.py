import re
from typing import List, Dict, Any

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
    print("DEBUG: 模擬 OCR 服務已調用。")
    # 這裡返回一個模擬的藥袋文字，模擬 OCR 辨識的結果
    # 根據你的流程圖，範例文字如下：
    mock_ocr_result = """
看診日期:114.06.12
本次發藥天數:3日份

藥品名稱 單次劑量 用藥頻率 主要用途 副作用
普拿疼 2 一日三次 止痛
脈優錠 1 一日三次 治療高血壓 嘔吐 頭暈
"""
    return mock_ocr_result.strip()

def convert_frequency_to_times(frequency_text: str) -> List[str]:
    """
    將用藥頻率的文字描述轉換為具體的服藥時間列表 (HH:MM 格式)。
    可以根據實際需求擴充或調整服藥時間。

    Args:
        frequency_text (str): 用藥頻率的文字描述，例如 "一日三次"。

    Returns:
        List[str]: 具體的服藥時間列表，例如 ["08:00", "14:00", "20:00"]。
    """
    frequency_text = frequency_text.strip()
    times = []

    if "一日一次" in frequency_text:
        times = ["09:00"] # 假設早上9點
    elif "一日兩次" in frequency_text:
        times = ["09:00", "18:00"] # 假設早上9點，晚上6點
    elif "一日三次" in frequency_text:
        times = ["08:00", "14:00", "20:00"] # 假設三餐飯後或定時
    elif "一日四次" in frequency_text:
        times = ["08:00", "12:00", "18:00", "22:00"]
    elif "睡前" in frequency_text:
        times = ["22:00"] # 假設晚上10點
    elif "需要時服用" in frequency_text or "需要時" in frequency_text:
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
                                        'frequency_text': '一日三次',
                                        'times': ['08:00', '14:00', '20:00'],
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
        if "藥品名稱" in line and "單次劑量" in line and "用藥頻率" in line:
            start_parsing = True
            continue

        if start_parsing and line.strip(): # 確保不是空行
            # 假設藥品資訊的順序是固定的：藥品名稱 劑量 用藥頻率 主要用途 副作用
            # 這裡使用更彈性的正則表達式來匹配多個單詞的藥品名稱和用途
            # 假設每個字段之間至少有一個空格
            # 範例行: 普拿疼 2 一日三次 止痛
            # 範例行: 脈優錠 1 一日三次 治療高血壓 嘔吐 頭暈

            # 嘗試使用正則表達式解析
            # 這裡的 regex 假設：
            # Group 1: 藥品名稱 (可以是中文或英文，多個字)
            # Group 2: 單次劑量 (數字)
            # Group 3: 用藥頻率 (中文，多個字)
            # Group 4: 主要用途 (中文，多個字，可能有多個詞)
            # Group 5: 副作用 (可選，中文，多個字，可能有多個詞)
            
            # 這個 regex 需要根據 OCR 的實際輸出格式調整，這裡是一個嘗試
            # 假設字段由至少一個空格分隔
            # 我們需要處理副作用可能為空的情況
            match = re.match(r"(\S+)\s+(\d+)\s+(\S+)\s+([^\s]+(?: [^\s]+)*)(?:\s+(.*))?", line.strip())

            if match:
                name = match.group(1)
                dosage = match.group(2)
                frequency_text = match.group(3)
                purpose = match.group(4)
                side_effects = match.group(5) if match.group(5) else ""

                times = convert_frequency_to_times(frequency_text)

                parsed_medications.append({
                    'name': name.strip(),
                    'dosage': dosage.strip(),
                    'frequency_text': frequency_text.strip(),
                    'times': times,
                    'purpose': purpose.strip(),
                    'side_effects': side_effects.strip()
                })
            else:
                print(f"WARN: 無法解析行: {line}")
                # 如果無法解析，可以選擇跳過或記錄錯誤
                pass

    return parsed_medications

