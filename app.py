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
        send_line_push(user_id, "❌ 系統錯誤：無法取得 TDX Token，請檢查環境變數設定。")
        return
    
    headers = {
        'authorization': f'Bearer {token}',
        'Accept': 'application/json'
    }

    try:
        # 1. 抓取路段對照表
        res_map = requests.get(SECTION_MAP_URL, headers=headers)
        map_data = res_map.json()
        
        # TDX 回傳有時會直接是 List，有時會包在 'Sections' 或其他 Key 裡
        if isinstance(map_data, list):
            df_map = pd.DataFrame(map_data)
        elif isinstance(map_data, dict) and 'Sections' in map_data:
            df_map = pd.DataFrame(map_data['Sections'])
        else:
            # 如果還是抓不到正確格式，將錯誤訊息傳回 LINE
            send_line_push(user_id, f"⚠️ 路段資料格式異常，請檢查 TDX 帳號權限。")
            return

        # 檢查欄位是否存在，預防 KeyError
        if 'StartDescription' not in df_map.columns:
            send_line_push(user_id, "❌ 無法從 API 讀取路段地名，請稍後再試。")
            return

        # 2. 模糊搜尋起訖點
        # 使用 na=False 防止資料中有空值導致報錯
        mask = df_map['StartDescription'].str.contains(start_point, na=False) & \
               df_map['EndDescription'].str.contains(end_point, na=False)
        target_ids = df_map[mask]['SectionID'].tolist()
        
        if not target_ids:
            send_line_push(user_id, f"❌ 找不到路段：從 {start_point} 到 {end_point}，請確認地名正確性。")
            return

        # 成功鎖定路段後，回傳確認訊息
        send_line_push(user_id, f"🚀 監控已啟動！\n路段：{start_point} ↔ {end_point}\n目標：{target_speed} km/h\n限時：{duration_hours} 小時")

        # 3. 進入監控循環
        end_time = datetime.now() + timedelta(hours=float(duration_hours))
        
        while datetime.now() < end_time:
            res_live = requests.get(LIVE_TRAFFIC_URL, headers=headers)
            if res_live.status_code == 200:
                live_data = res_live.json()
                # 確保取到 LiveTraffics 內的資料
                if isinstance(live_data, dict) and 'LiveTraffics' in live_data:
                    df_live = pd.DataFrame(live_data['LiveTraffics'])
                else:
                    df_live = pd.DataFrame(live_data)
                
                if not df_live.empty and 'SectionID' in df_live.columns:
                    my_segment = df_live[df_live['SectionID'].isin(target_ids)].copy()
                    
                    if not my_segment.empty:
                        my_segment['TravelSpeed'] = pd.to_numeric(my_segment['TravelSpeed'], errors='coerce')
                        current_avg = my_segment['TravelSpeed'].mean()
                        
                        # 只有當平均時速達標才通知
                        if current_avg >= float(target_speed):
                            send_line_push(user_id, f"🎉 【達標通知】\n目前 {start_point}-{end_point} 平均時速：{current_avg:.1f} km/h\n現在出發正好，一路順風！")
                            return 
            
            # 每 8 分鐘檢查一次
            time.sleep(480) 
        
        send_line_push(user_id, f"⏰ 監控逾時：已監控 {duration_hours} 小時仍未達標，系統自動停止任務。")

    except Exception as e:
        # 攔截所有意外錯誤並傳回 LINE，方便 Debug
        send_line_push(user_id, f"🚨 監控過程發生錯誤：{str(e)}")
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
    # 從環境變數取得 Render 分配的 Port，預設為 5000
    port = int(os.environ.get("PORT", 5000))
    # 務必設定 host='0.0.0.0' 才能讓外部連線進來
    app.run(host='0.0.0.0', port=port)


