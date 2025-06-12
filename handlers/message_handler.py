from linebot.models import (
    TextSendMessage, FlexSendMessage,
    QuickReply, QuickReplyButton, # 確保 QuickReply, QuickReplyButton 已導入
    MessageAction, URIAction # 確保 MessageAction, URIAction 已導入
)
from models import create_user_if_not_exists, bind_family, generate_invite_code
import re
from urllib.parse import quote # 確保 quote 已導入

def handle_text_message(event, line_bot_api):
    user_id = event.source.user_id
    text = event.message.text.strip()

    print(f"✅ 收到使用者訊息：{text}（來自 {user_id}）") #

    create_user_if_not_exists(user_id) #

    # 綁定邀請碼
    match = re.match(r"^綁定\s*(\S+)", text) # 調整正規表達式，\S+ 匹配非空白字元，更彈性
    if match: #
        code = match.group(1) #
        print(f"🧪 嘗試綁定邀請碼：{code}") #
        
        # 根據 models.py 的 bind_family(invite_code, family_user_id) 調整參數順序
        success, elder_id = bind_family(code, user_id) #
        if success:
            try:
                profile = line_bot_api.get_profile(elder_id)
                elder_display_name = profile.display_name
            except Exception:
                elder_display_name = "您的家人" # 如果無法獲取名稱，使用預設值

            reply = f"✅ 綁定成功！您將收到 {elder_display_name} 的用藥通知。" #
        else: #
            reply = "❌ 邀請碼無效或已使用。" #
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply)) #
        return
    elif text == "綁定": # 處理只輸入「綁定」的情況
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text="❗ 請提供有效的邀請碼，格式：綁定 [邀請碼]。"))
        return


    # 產生邀請碼
    if text == "產生邀請碼": #
        invite_code = generate_invite_code(user_id)

        # 創建 Flex Message
        flex_message = FlexSendMessage(
            alt_text="您的邀請碼資訊", #
            contents={ #
                "type": "bubble", #
                "body": { #
                    "type": "box", #
                    "layout": "vertical", #
                    "contents": [ #
                        {
                            "type": "text", #
                            "text": "✅ 邀請碼已產生", #
                            "weight": "bold", #
                            "size": "xl", #
                            "margin": "md", #
                            "align": "center", #
                        },
                        {"type": "separator", "margin": "lg"}, #
                        {
                            "type": "box", #
                            "layout": "vertical", #
                            "margin": "lg", #
                            "spacing": "sm", #
                            "contents": [ #
                                {
                                    "type": "box", #
                                    "layout": "baseline", #
                                    "spacing": "sm", #
                                    "contents": [ #
                                        {
                                            "type": "text", #
                                            "text": "邀請碼：", #
                                            "color": "#aaaaaa", #
                                            "size": "sm", #
                                            "flex": 2, #
                                        },
                                        {
                                            "type": "text", #
                                            "text": invite_code, #
                                            "wrap": True, #
                                            "color": "#666666", #
                                            "size": "sm", #
                                            "flex": 5, #
                                            "weight": "bold", #
                                        },
                                    ],
                                }
                            ],
                        },
                        {
                            "type": "button", #
                            "action": { #
                                "type": "postback", #
                                "label": "分享給家人", #
                                "data": f"action=share_invite&code={invite_code}", #
                                "displayText": "點擊複製邀請連結", #
                            },
                            "style": "primary", #
                            "color": "#00B900", #
                            "margin": "xl", #
                        },
                    ],
                },
                "footer": { #
                    "type": "box", #
                    "layout": "vertical", #
                    "contents": [ #
                        {
                            "type": "text", #
                            "text": "點擊上方按鈕獲取邀請訊息", #
                            "size": "xs", #
                            "color": "#aaaaaa", #
                            "align": "center", #
                            "margin": "md", #
                        }
                    ],
                },
            },
        )

        line_bot_api.reply_message(event.reply_token, flex_message) #
        return

    if text == "我的藥單": #
        reply_text = text #
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text)) #
        return 

    if text == "藥品查詢": #
        reply_text = text #
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text)) #
        return

    if text == "我的藥歷": #
        reply_text = text #
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text)) #
        return

    if text == "我的健康紀錄": #
        reply_text = text #
        line_bot_api.reply_message(event.reply_token, TextSendMessage(text=reply_text)) #
        return

    # 預設訊息
    line_bot_api.reply_message(
        event.reply_token, TextSendMessage(text="請透過主選單選取相關功能進行操作") #
    )
    return