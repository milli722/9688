import os
import threading
import time
import requests
import pandas as pd
from flask import Flask, request, abort
from datetime import datetime, timedelta

app = Flask(__name__)

# --- 從環境變數讀取金鑰 (之後在 Render 設定) ---
TDX_CLIENT_ID = os.environ.get('TDX_CLIENT_ID')
TDX_CLIENT_SECRET = os.environ.get('TDX_CLIENT_SECRET')
LINE_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN')

# API 網址
SECTION_MAP_URL = "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Section/Freeway?%24top=500&%24format=JSON"
LIVE_TRAFFIC_URL = "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Live/Freeway?%24select=SectionID%2CTravelSpeed&%24top=150&%24format=JSON"

def get_tdx_token():
    auth_url = "https://tdx.transportdata.tw/auth/realms/TDXConnect/protocol/openid-connect/token"
    payload = {'grant_type': 'client_credentials', 'client_id': TDX_CLIENT_ID, 'client_secret': TDX_CLIENT_SECRET}
    try:
        res = requests.post(auth_url, data=payload)
        return res.json().get('access_token')
    except:
        return None

def send_line_push(user_id, msg):
    url = "https://api.line.me/v2/bot/message/push"
    headers = {"Content-Type": "application/json", "Authorization": f"Bearer {LINE_ACCESS_TOKEN}"}
    payload = {"to": user_id, "messages": [{"type": "text", "text": msg}]}
    requests.post(url, headers=headers, json=payload)

# --- 監控邏輯 ---
def monitor_task(user_id, start_point, end_point, target_speed, duration_hours):
    token = get_tdx_token()
    if not token: 
        send_line_push(user_id, "❌ 系統錯誤：無法取得 TDX Token")
        return
    
    headers = {'authorization': f'Bearer {token}'}
    res_map = requests.get(SECTION_MAP_URL, headers=headers)
    df_map = pd.DataFrame(res_map.json())
    
    # 模糊搜尋起訖點
    mask = df_map['StartDescription'].str.contains(start_point) & df_map['EndDescription'].str.contains(end_point)
    target_ids = df_map[mask]['SectionID'].tolist()
    
    if not target_ids:
        send_line_push(user_id, f"❌ 找不到路段：{start_point}-{end_point}，請確認地名是否精確。")
        return

    send_line_push(user_id, f"🚀 監控已啟動！\n路段：{start_point} 到 {end_point}\n目標：{target_speed} km/h\n限時：{duration_hours} 小時")

    end_time = datetime.now() + timedelta(hours=float(duration_hours))
    
    while datetime.now() < end_time:
        res_live = requests.get(LIVE_TRAFFIC_URL, headers=headers)
        if res_live.status_code == 200:
            data = res_live.json().get('LiveTraffics', [])
            df_live = pd.DataFrame(data)
            my_segment = df_live[df_live['SectionID'].isin(target_ids)].copy()
            
            if not my_segment.empty:
                my_segment['TravelSpeed'] = pd.to_numeric(my_segment['TravelSpeed'], errors='coerce')
                current_avg = my_segment['TravelSpeed'].mean()
                
                if current_avg >= float(target_speed):
                    send_line_push(user_id, f"🎉 【達標通知】\n目前 {start_point}-{end_point} 平均時速：{current_avg:.1f} km/h\n符合您的期待，可以出發了！")
                    return 
        
        time.sleep(480) # 每 8 分鐘檢查一次
    
    send_line_push(user_id, f"⏰ 監控逾時：已持續 {duration_hours} 小時未達標，任務結束。")

@app.route("/callback", methods=['POST'])
def callback():
    body = request.get_json()
    try:
        # 解析 LINE 訊息
        event = body['events'][0]
        user_id = event['source']['userId']
        user_msg = event['message']['text']
        
        # 預期格式: 國1,桃園,內壢,80,2
        params = user_msg.split(',')
        if len(params) == 5:
            # 啟動 Thread 背景執行
            t = threading.Thread(target=monitor_task, args=(user_id, params[1], params[2], params[3], params[4]))
            t.start()
            return 'OK'
    except:
        pass
    return 'OK'

@app.route("/", methods=['GET'])
def index():
    return "Bot is running!"

if __name__ == "__main__":
    app.run()