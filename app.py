import os
import threading
import time
import requests
import pandas as pd
from flask import Flask, request, abort
from datetime import datetime, timedelta

app = Flask(__name__)

# 環境變數讀取金鑰
TDX_CLIENT_ID = os.environ.get('TDX_CLIENT_ID')
TDX_CLIENT_SECRET = os.environ.get('TDX_CLIENT_SECRET')
LINE_ACCESS_TOKEN = os.environ.get('LINE_ACCESS_TOKEN')

# API 
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
        res_map = requests.get(SECTION_MAP_URL, headers=headers, timeout=20)
        data = res_map.json()
        
   
        if isinstance(data, dict):
            for k in ['Sections', 'value', 'data']:
                if k in data:
                    df_map = pd.DataFrame(data[k])
                    break
            else:
                df_map = pd.DataFrame(data)
        else:
            df_map = pd.DataFrame(data)

        # 2. 自動偵測地名欄位
        name_col = None
        for col in ['SectionName', 'StartDescription', 'SectionID']:
            if col in df_map.columns:
                name_col = col
                break
        
        if not name_col:
            send_line_push(user_id, f"無法辨識資料欄位，請檢查 API 回傳格式")
            return

        # 3. 索引定位
        start_mask = df_map[name_col].str.contains(start_point, na=False)
        end_mask = df_map[name_col].str.contains(end_point, na=False)

        if not start_mask.any() or not end_mask.any():
            send_line_push(user_id, f"找不到「{start_point}」或「{end_point}」，請確認名稱正確")
            return

        # 取得範圍索引
        start_idx = df_map[start_mask].index.min()
        end_idx = df_map[end_mask].index.max()
        idx_min, idx_max = min(start_idx, end_idx), max(start_idx, end_idx)
        
        # 抓出範圍內所有 SectionID
        target_ids = df_map.iloc[idx_min : idx_max + 1]['SectionID'].tolist()
        
        send_line_push(user_id, f"🚀 全線監控啟動！\n範圍：{start_point} ↔ {end_point}\n包含 {len(target_ids)} 個細分路段\n目標平均時速：{target_speed} km/h")

        # 4. 監控
        end_time = datetime.now() + timedelta(hours=float(duration_hours))
        while datetime.now() < end_time:
            res_live = requests.get(LIVE_TRAFFIC_URL, headers=headers, timeout=20)
            live_json = res_live.json()
            
            # 即時資料
            df_live = pd.DataFrame()
            if isinstance(live_json, dict):
                for k in ['LiveTraffics', 'value']:
                    if k in live_json:
                        df_live = pd.DataFrame(live_json[k])
                        break
            else:
                df_live = pd.DataFrame(live_json)

            if not df_live.empty and 'SectionID' in df_live.columns:
                # 過濾出該範圍內的所有路段時速
                route_data = df_live[df_live['SectionID'].isin(target_ids)].copy()
                if not route_data.empty:
                    route_data['TravelSpeed'] = pd.to_numeric(route_data['TravelSpeed'], errors='coerce')
                    avg_speed = route_data['TravelSpeed'].mean()
                    
                    if avg_speed >= float(target_speed):
                        send_line_push(user_id, f"🎉 【全線達標】\n{start_point}-{end_point} 平均時速：{avg_speed:.1f} km/h\n目前路況順暢，可以出發，別拖拖拉拉！")
                        return 
            
            time.sleep(480) # 檢查
            
        send_line_push(user_id, f"監控時限 ({duration_hours}hr) 已到，任務結束\n若需繼續監控請重新發送需求")

    except Exception as e:
        send_line_push(user_id, f"系統異常：{str(e)}")
@app.route("/callback", methods=['POST'])
def callback():
    body = request.get_json()
    try:
        # LINE 訊息
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
    # 讓外部連線進來
    app.run(host='0.0.0.0', port=port)











