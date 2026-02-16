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
SECTION_MAP_URL = "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Section/Freeway?%24format=JSON"
LIVE_TRAFFIC_URL = "https://tdx.transportdata.tw/api/basic/v2/Road/Traffic/Live/Freeway?%24format=JSON"

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

def monitor_task(user_id, start_point, end_point, target_speed, duration_hours):
    token = get_tdx_token()
    if not token: 
        send_line_push(user_id, "系統錯誤：無法取得 Token")
        return
    
    headers = {'authorization': f'Bearer {token}', 'Accept': 'application/json'}

    try:
        # 1. 抓取全台路段對照表
        res_map = requests.get(SECTION_MAP_URL, headers=headers, timeout=15)
        df_map = pd.DataFrame(res_map.json()) # 假設已解包

        # 2. 【核心邏輯】找出起點與終點的索引位置
        # 尋找包含起點的第一個路段
        start_mask = df_map['SectionName'].str.contains(start_point, na=False)
        # 尋找包含終點的第一個路段
        end_mask = df_map['SectionName'].str.contains(end_point, na=False)

        if not start_mask.any() or not end_mask.any():
            send_line_push(user_id, f"找不到起點「{start_point}」或終點「{end_point}」，請輸入正確格式或道路名稱")
            return

        # 取得索引序號
        start_idx = df_map[start_mask].index.min()
        end_idx = df_map[end_mask].index.max()

        # 確保索引順序（處理南下北上）
        idx_min, idx_max = min(start_idx, end_idx), max(start_idx, end_idx)
        
        # 抓出範圍內所有的 SectionID
        target_ids = df_map.iloc[idx_min : idx_max + 1]['SectionID'].tolist()
        route_desc = f"{start_point} ↔ {end_point} (共 {len(target_ids)} 個路段)"

        send_line_push(user_id, f"長途監控啟動！\n範圍：{route_desc}\n目標平均：{target_speed} km/h")

        # 3. 進入監控循環
        end_time = datetime.now() + timedelta(hours=float(duration_hours))
        while datetime.now() < end_time:
            res_live = requests.get(LIVE_TRAFFIC_URL, headers=headers, timeout=15)
            live_data = res_live.json()
            df_live = pd.DataFrame(live_data) # 假設已解包
            
            if not df_live.empty:
                # 過濾出整個範圍路徑的資料
                my_route = df_live[df_live['SectionID'].isin(target_ids)].copy()
                if not my_route.empty:
                    my_route['TravelSpeed'] = pd.to_numeric(my_route['TravelSpeed'], errors='coerce')
                    # 計算整段路的「平均時速」
                    total_avg_speed = my_route['TravelSpeed'].mean()
                    
                    if total_avg_speed >= float(target_speed):
                        send_line_push(user_id, f"🎉 【全線通暢通知】\n{start_point} 到 {end_point} 全段平均時速：{total_avg_speed:.1f} km/h\n，可以出發了，別拖了！")
                        return 
            
            time.sleep(480) 
        send_line_push(user_id, " 監控時限已到，任務結束。")

    except Exception as e:
        send_line_push(user_id, f"系統異常：{str(e)}")
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









