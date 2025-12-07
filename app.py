import os
import json
import re
import pandas as pd
import numpy as np  # 新增 numpy 以便處理型別
import gspread
from flask import Flask, request, jsonify
from google.oauth2.service_account import Credentials
from gspread_formatting import *

app = Flask(__name__)

# --- 🔧 初始化與設定 ---
def get_sh():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    if not creds_json or not sheet_id:
        raise Exception("環境變數設定錯誤：找不到 GOOGLE_CREDENTIALS 或 SHEET_ID")

    creds_dict = json.loads(creds_json)
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)

# --- 🛠️ 輔助函數 ---
def parse_route_options(value_str):
    if not value_str or pd.isna(value_str):
        return []
    matches = re.findall(r'(\d+)%([^\s,]+)', str(value_str))
    options = []
    for pct, label in matches:
        options.append({'percentage': int(pct), 'label': label})
    options.sort(key=lambda x: x['percentage'], reverse=True)
    return options

def format_date(val):
    if pd.isna(val) or val == '': return ''
    try:
        return pd.to_datetime(val).strftime('%Y-%m-%d')
    except:
        return str(val)

def deduplicate_headers(headers):
    """
    將重複的標題加上後綴 _1, _2，確保欄位名稱唯一
    """
    seen = {}
    new_headers = []
    for i, h in enumerate(headers):
        h = str(h).strip()
        if h == "": h = f"Col_{i+1}"
        
        if h in seen:
            seen[h] += 1
            new_headers.append(f"{h}_{seen[h]}")
        else:
            seen[h] = 0
            new_headers.append(h)
    return new_headers

# --- 🧹 步驟 1: 清除桌面 ---
def step1_clear(sh, mode='A'):
    keep_sheets = {
        '託收託運回報', 'GAI每日訂單分析', '5678月貨主收送點參照', '5678月班別路線參照', 
        '參照', '碳排', '配送地址參照', '低碳路線表', '退貨表', '託收托運點資訊', 
        '託收托運點資訊(簡)', '指定日期'
    }
    
    worksheets = sh.worksheets()
    deleted_count = 0
    
    for ws in worksheets:
        name = ws.title
        should_delete = False
        
        if mode == 'D':
            if '(D)' in name or name == '託收託運回報_篩選(D)':
                should_delete = True
        else:
            if name not in keep_sheets:
                if any(k in name for k in ['篩選', '路線表', '板數', '(B)', '(C)', '(D)']):
                    should_delete = True
        
        if should_delete:
            try:
                sh.del_worksheet(ws)
                deleted_count += 1
            except:
                pass
                
    return f"[{mode}模態] 已清除 {deleted_count} 個工作表"

# --- 📅 步驟 2: 日期篩選 ---
def step2_filter(sh, mode='A'):
    suffix = ""
    if mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    target_sheet_name = f'託收託運回報_篩選{suffix}'
    
    ws_date = sh.worksheet('指定日期')
    date_cell = 'A3' if mode == 'D' else 'A2'
    target_date_val = ws_date.acell(date_cell).value
    target_date = format_date(target_date_val)
    
    if not target_date:
        return f"錯誤：指定日期工作表 ({date_cell}) 未設定日期"

    ws_source = sh.worksheet('託收託運回報')
    raw_data = ws_source.get_all_values()
    if not raw_data: return "錯誤：來源表是空的"

    headers = deduplicate_headers(raw_data[0])
    df = pd.DataFrame(raw_data[1:], columns=headers)
    
    date_col_idx = 5
    df['fmt_date'] = df.iloc[:, date_col_idx].apply(format_date)
    filtered_df = df[df['fmt_date'] == target_date].drop(columns=['fmt_date'])
    
    if filtered_df.empty:
        return f"錯誤：找不到日期 {target_date} 的資料"

    try:
        ws_target = sh.worksheet(target_sheet_name)
        ws_target.clear()
        if ws_target.col_count < 40:
            ws_target.resize(cols=40)
    except:
        ws_target = sh.add_worksheet(target_sheet_name, rows=1000, cols=40)
    
    # 🔥 關鍵修正：將所有資料轉為字串，避免 int64 錯誤
    filtered_df = filtered_df.fillna('').astype(str)
    
    update_data = [filtered_df.columns.values.tolist()] + filtered_df.values.tolist()
    ws_target.update(update_data)
    
    return f"[{mode}模態] 篩選完成，共 {len(filtered_df)} 筆"

# --- 🛣️ 步驟 3: 路線比對 + APP3 ---
def step3_mapping(sh, mode='A'):
    suffix = ""
    if mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    sheet_name = f'託收託運回報_篩選{suffix}'
    ws = sh.worksheet(sheet_name)
    
    if ws.col_count < 40:
        ws.resize(cols=40)
    
    raw_data = ws.get_all_values()
    if not raw_data: return "錯誤：工作表是空的"
    
    headers = deduplicate_headers(raw_data[0])
    df = pd.DataFrame(raw_data[1:], columns=headers)
    
    ws_ref = sh.worksheet('5678月貨主收送點參照')
    ref_data = ws_ref.get_all_values()
    df_ref = pd.DataFrame(ref_data[1:], columns=deduplicate_headers(ref_data[0]))
    
    mapping = {}
    for _, row in df_ref.iterrows():
        key = f"{str(row.iloc[0]).strip()}|{str(row.iloc[1]).strip()}"
        mapping[key] = {'C': str(row.iloc[2]), 'D': str(row.iloc[3])}
    
    ws_code_ref = sh.worksheet('參照')
    code_data = ws_code_ref.get_all_values()
    df_code = pd.DataFrame(code_data[1:], columns=deduplicate_headers(code_data[0]))
    
    map_ab = dict(zip(df_code.iloc[:, 0].astype(str).str.strip(), df_code.iloc[:, 1]))
    map_cd = dict(zip(df_code.iloc[:, 2].astype(str).str.strip(), df_code.iloc[:, 3]))
    map_ef = dict(zip(df_code.iloc[:, 4].astype(str).str.strip(), df_code.iloc[:, 5]))

    col_owner_idx = 4
    col_h_idx = 7
    
    x_values = []
    ah_values = []
    ac_values = []
    aa_values = []
    ab_values = []
    
    for _, row in df.iterrows():
        owner = str(row.iloc[col_owner_idx]).strip()
        h_val = str(row.iloc[col_h_idx]).strip()
        if h_val.startswith('(預)'): h_val = h_val[3:]
        
        key = f"{owner}|{h_val}"
        primary_route = ''
        secondary_route = ''
        
        if key in mapping:
            ref_data = mapping[key]
            opts_c = parse_route_options(ref_data['C'])
            opts_d = parse_route_options(ref_data['D'])
            all_opts = sorted(opts_c + opts_d, key=lambda x: x['percentage'], reverse=True)
            
            if all_opts:
                primary_route = all_opts[0]['label']
                if mode == 'B' and len(all_opts) > 1 and all_opts[1]['percentage'] > 40:
                    secondary_route = all_opts[1]['label']
        
        x_values.append(primary_route)
        ah_values.append(secondary_route)
        
        x_str = str(primary_route).strip()
        val_ac = map_ab.get(x_str[:5], '') if len(x_str) >= 5 else ''
        ac_values.append(val_ac)
        val_aa = map_cd.get(x_str[0], '') if len(x_str) >= 1 else ''
        aa_values.append(val_aa)
        val_ab = map_ef.get(x_str[2], '') if len(x_str) >= 3 else ''
        ab_values.append(val_ab)

    ws.update('X2', [[x] for x in x_values])
    
    if mode == 'B':
        ws.update('AH2', [[x] for x in ah_values])
    else:
        empty_col = [[''] for _ in range(len(x_values))]
        ws.update('AH2', empty_col)
        
    ws.update('AI2', [[''] for _ in range(len(x_values))])
    ws.update('AA2', [[x] for x in aa_values])
    ws.update('AB2', [[x] for x in ab_values])
    ws.update('AC2', [[x] for x in ac_values])
    
    return f"[{mode}模態] 路線比對 & APP3 映射完成"

# --- 📊 步驟 4: 創建工作表 ---
def step4_create(sh, mode='A'):
    suffix = ""
    if mode == 'B': suffix = "(B)"
    elif mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    src_name = f'託收託運回報_篩選{suffix if mode != "B" else ""}'
    dst_name = f'當日各路線表{suffix}'
    summary_name = f'各路線板數{suffix}'
    
    ws_src = sh.worksheet(src_name)
    raw_data = ws_src.get_all_values()
    if not raw_data: return "錯誤：來源表是空的"
    
    headers = deduplicate_headers(raw_data[0])
    df = pd.DataFrame(raw_data[1:], columns=headers)
    
    df = df[~df.iloc[:, 7].astype(str).str.contains('昶青', na=False)]
    
    series_x = df.iloc[:, 23]
    if df.shape[1] > 33:
        series_ah = df.iloc[:, 33]
    else:
        series_ah = pd.Series([''] * len(df), index=df.index)
        
    routes = set(series_x[series_x != ''].unique())
    if mode == 'B':
        routes.update(series_ah[series_ah != ''].unique())
    
    routes = sorted([r for r in routes if r and str(r).strip() != ''])
    
    final_rows = []
    final_rows.append(headers)
    summary_rows = [['路線名稱', '板數總和', '取貨', '配送']]
    
    df['_TEMP_X'] = series_x
    df['_TEMP_AH'] = series_ah
    
    for route in routes:
        if mode == 'B':
            mask = (df['_TEMP_X'] == route) | (df['_TEMP_AH'] == route)
        else:
            mask = (df['_TEMP_X'] == route)
            
        group = df[mask]
        if group.empty: continue
        
        title_row = [''] * len(headers)
        title_row[0] = route
        final_rows.append(title_row)
        
        # 🔥 關鍵修正：將 group 資料轉為字串，避免 int64
        group_values = group.iloc[:, :len(headers)].fillna('').astype(str).values.tolist()
        final_rows.extend(group_values)
        
        col_board_idx = 17
        # 計算總和
        total_boards_val = pd.to_numeric(group.iloc[:, col_board_idx], errors='coerce').fillna(0).sum()
        # 🔥 關鍵修正：強制轉為 Python int
        total_boards = int(total_boards_val)
        
        sum_row = [''] * len(headers)
        sum_row[col_board_idx] = f"總和: {total_boards}"
        final_rows.append(sum_row)
        
        col_type_idx = 6
        col_cust_idx = 7
        pickup_map = {}
        delivery_map = {}
        
        for _, row in group.iterrows():
            ctype = str(row.iloc[col_type_idx])
            cust = str(row.iloc[col_cust_idx])
            # 🔥 關鍵修正：強制轉為 Python int
            boards = int(pd.to_numeric(row.iloc[col_board_idx], errors='coerce') or 0)
            
            if ctype == '取貨':
                pickup_map[cust] = pickup_map.get(cust, 0) + boards
            elif ctype == '配送':
                delivery_map[cust] = delivery_map.get(cust, 0) + boards
                
        pickup_str = ", ".join([f"{k} ({v})" for k, v in pickup_map.items()])
        delivery_str = ", ".join([f"{k} ({v})" for k, v in delivery_map.items()])
        summary_rows.append([route, total_boards, pickup_str, delivery_str])
        
    try:
        ws_dst = sh.worksheet(dst_name)
        ws_dst.clear()
    except:
        ws_dst = sh.add_worksheet(dst_name, rows=len(final_rows)+100, cols=len(headers))
    ws_dst.update(final_rows)
    
    try:
        ws_sum = sh.worksheet(summary_name)
        ws_sum.clear()
    except:
        ws_sum = sh.add_worksheet(summary_name, rows=len(summary_rows)+20, cols=5)
    ws_sum.update(summary_rows)
    
    return f"[{mode}模態] 已建立 {dst_name} 與 {summary_name}"

@app.route('/', methods=['GET'])
def home():
    return "物流 AI 系統運作中 (JSON Fix)"

@app.route('/execute', methods=['POST'])
def execute():
    try:
        data = request.json
        action = data.get('action')
        mode = data.get('mode', 'A')
        sh = get_sh()
        msg = ""
        if action == 'step1': msg = step1_clear(sh, mode)
        elif action == 'step2': msg = step2_filter(sh, mode)
        elif action == 'step3': msg = step3_mapping(sh, mode)
        elif action == 'step4': msg = step4_create(sh, mode)
        elif action == 'all':
            msgs = []
            if mode == 'A': msgs.append(step1_clear(sh, mode))
            msgs.append(step2_filter(sh, mode))
            msgs.append(step3_mapping(sh, mode))
            msgs.append(step4_create(sh, mode))
            msg = " -> ".join(msgs)
        return jsonify({"status": "success", "message": msg})
    except Exception as e:
        print(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
