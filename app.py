import os
import json
import re
import pandas as pd
import gspread
from flask import Flask, request, jsonify
from google.oauth2.service_account import Credentials

app = Flask(__name__)

# --- 🔧 初始化設定 ---
def get_sh():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    sheet_id = os.environ.get('SHEET_ID')
    
    if not creds_json or not sheet_id:
        raise Exception("環境變數設定錯誤：找不到憑證或 Sheet ID")

    creds_dict = json.loads(creds_json)
    scopes = ['https://www.googleapis.com/auth/spreadsheets', 'https://www.googleapis.com/auth/drive']
    creds = Credentials.from_service_account_info(creds_dict, scopes=scopes)
    client = gspread.authorize(creds)
    return client.open_by_key(sheet_id)

# --- 🛠️ 輔助工具：解析百分比字串 (給 Mode B 用) ---
# 對應 GAS: parsePercentageStrings
def parse_route_options(value_str):
    if not value_str or pd.isna(value_str):
        return []
    # 尋找所有 "60%早-北一" 格式
    matches = re.findall(r'(\d+)%([^\s,]+)', str(value_str))
    options = []
    for pct, label in matches:
        options.append({'percentage': int(pct), 'label': label})
    # 依照百分比由大到小排序
    options.sort(key=lambda x: x['percentage'], reverse=True)
    return options

def format_label(option, full_str):
    # 簡單判斷前綴，這裡簡化處理，直接回傳 label
    # 原本邏輯是判斷 C/D 欄位決定前綴，這裡假設 label 本身就包含路線名
    return option['label']

# --- 🧹 步驟 1: 清除桌面 (通用) ---
def step1_clear(sh):
    keep_list = {
        '託收託運回報', 'GAI每日訂單分析', '5678月貨主收送點參照', '5678月班別路線參照', 
        '參照', '碳排', '配送地址參照', '低碳路線表', '退貨表', '託收托運點資訊', 
        '託收托運點資訊(簡)', '指定日期'
    }
    worksheets = sh.worksheets()
    count = 0
    for ws in worksheets:
        # 刪除所有 "篩選", "路線表", "板數" 相關且不在保留名單的表
        if ws.title not in keep_list:
            if any(k in ws.title for k in ['篩選', '路線表', '板數', '(B)', '(C)', '(D)']):
                try:
                    sh.del_worksheet(ws)
                    count += 1
                except:
                    pass
    return f"已清除 {count} 個暫存工作表"

# --- 📅 步驟 2: 日期篩選 (支援 A/B/C/D) ---
def step2_filter(sh, mode='A'):
    # 設定後綴
    suffix = ""
    if mode == 'B': suffix = "" # B模態通常共用篩選表，或視需求調整
    elif mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    target_sheet_name = f'託收託運回報_篩選{suffix}'
    
    # 1. 讀取日期
    ws_date = sh.worksheet('指定日期')
    # D模態讀 A3, 其他讀 A2
    date_cell = 'A3' if mode == 'D' else 'A2'
    target_date = ws_date.acell(date_cell).value
    
    if not target_date:
        return f"錯誤：指定日期工作表 ({date_cell}) 未設定日期"

    # 2. 讀取原始資料
    ws_source = sh.worksheet('託收託運回報')
    data = ws_source.get_all_values()
    headers = data[0]
    df = pd.DataFrame(data[1:], columns=headers)
    
    # 3. 篩選 (假設日期在第 6 欄, index 5)
    # 這裡做字串包含比對
    mask = df.iloc[:, 5].astype(str).str.contains(target_date, na=False)
    filtered_df = df[mask]
    
    if filtered_df.empty:
        return f"錯誤：找不到日期 {target_date} 的資料"

    # 4. 寫入結果
    try:
        ws_target = sh.worksheet(target_sheet_name)
        ws_target.clear()
    except:
        ws_target = sh.add_worksheet(target_sheet_name, rows=1000, cols=len(headers)+10)
    
    update_data = [filtered_df.columns.values.tolist()] + filtered_df.values.tolist()
    ws_target.update(update_data)
    
    return f"[{mode}模態] 篩選完成，共 {len(filtered_df)} 筆"

# --- 🛣️ 步驟 3: 路線比對 (支援 A/B/C/D 邏輯差異) ---
def step3_mapping(sh, mode='A'):
    suffix = ""
    if mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    sheet_name = f'託收託運回報_篩選{suffix}'
    ws = sh.worksheet(sheet_name)
    
    # 讀取資料
    df = pd.DataFrame(ws.get_all_records())
    
    # 讀取參照表
    ws_ref = sh.worksheet('5678月貨主收送點參照')
    df_ref = pd.DataFrame(ws_ref.get_all_records())
    
    # 建立映射字典
    # 假設參照表欄位順序: 貨主(0), 收送點(1), C欄(2), D欄(3)
    # 請務必確認你的 Google Sheet 參照表的標題名稱
    ref_cols = df_ref.columns
    mapping = {}
    
    for _, row in df_ref.iterrows():
        key = f"{str(row[ref_cols[0]]).strip()}|{str(row[ref_cols[1]]).strip()}"
        mapping[key] = {
            'C': str(row[ref_cols[2]]), 
            'D': str(row[ref_cols[3]])
        }
    
    # 開始比對
    # 假設回報表欄位: 貨主(4), H欄(7)
    rep_cols = df.columns
    col_owner = rep_cols[4]
    col_h = rep_cols[7]
    
    x_values = []  # 主路線 (第24欄)
    ah_values = [] # 副路線 (第34欄, 僅 Mode B 用)
    
    for _, row in df.iterrows():
        owner = str(row[col_owner]).strip()
        h_val = str(row[col_h]).strip().replace('(預)', '')
        key = f"{owner}|{h_val}"
        
        primary_route = ''
        secondary_route = ''
        
        if key in mapping:
            ref_data = mapping[key]
            # 解析 C 和 D 欄的所有選項
            opts_c = parse_route_options(ref_data['C'])
            opts_d = parse_route_options(ref_data['D'])
            all_opts = sorted(opts_c + opts_d, key=lambda x: x['percentage'], reverse=True)
            
            if all_opts:
                # 第一名給 X 欄
                primary_route = all_opts[0]['label']
                # 如果是 Mode B，且第二名 > 40%，給 AH 欄
                if mode == 'B' and len(all_opts) > 1 and all_opts[1]['percentage'] > 40:
                    secondary_route = all_opts[1]['label']
                    
        x_values.append(primary_route)
        ah_values.append(secondary_route)

    # 寫入資料
    # X 欄 (第24欄)
    ws.update('X2', [[x] for x in x_values])
    
    # AH 欄 (第34欄) - 只有 Mode B 需要寫入，其他模態清空
    if mode == 'B':
        ws.update('AH2', [[x] for x in ah_values])
    else:
        # 清空 AH (如果有的話)
        empty_col = [[''] for _ in range(len(x_values))]
        ws.update('AH2', empty_col)

    # --- 執行 APP3 (代碼映射 - 所有模態都要做) ---
    # 映射 X 欄的結果到 AC, AA, AB
    ws_code = sh.worksheet('參照')
    df_code = pd.DataFrame(ws_code.get_all_records())
    
    # 建立 APP3 映射表
    # 假設欄位順序: A->B, C->D, E->F
    code_cols = df_code.columns
    map_ab = dict(zip(df_code[code_cols[0]].astype(str).str.strip(), df_code[code_cols[1]]))
    map_cd = dict(zip(df_code[code_cols[2]].astype(str).str.strip(), df_code[code_cols[3]]))
    map_ef = dict(zip(df_code[code_cols[4]].astype(str).str.strip(), df_code[code_cols[5]]))
    
    ac_res = [] # 前5字
    aa_res = [] # 第1字
    ab_res = [] # 第3字
    
    for x in x_values:
        x_str = str(x).strip()
        
        # AC (前5字)
        val_ac = map_ab.get(x_str[:5], '') if len(x_str) >= 5 else ''
        ac_res.append(val_ac)
        
        # AA (第1字)
        val_aa = map_cd.get(x_str[0], '') if len(x_str) >= 1 else ''
        aa_res.append(val_aa)
        
        # AB (第3字)
        val_ab = map_ef.get(x_str[2], '') if len(x_str) >= 3 else ''
        ab_res.append(val_ab)
        
    # 寫回 AC(29), AA(27), AB(28)
    ws.update('AC2', [[x] for x in ac_res])
    ws.update('AA2', [[x] for x in aa_res])
    ws.update('AB2', [[x] for x in ab_res])
    
    return f"[{mode}模態] 路線比對 & APP3 完成"

# --- 📊 步驟 4: 創建工作表 (支援 A/B/C/D) ---
def step4_create(sh, mode='A'):
    suffix = ""
    if mode == 'B': suffix = "(B)"
    elif mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    src_name = f'託收託運回報_篩選{suffix if mode != "B" else ""}' # B模態來源通常是無後綴的篩選表
    dst_name = f'當日各路線表{suffix}'
    
    ws_src = sh.worksheet(src_name)
    df = pd.DataFrame(ws_src.get_all_records())
    
    # 抓取 X 欄 (假設標題為 'Route' 或類似，這裡用 index 23 保險)
    # 如果有標題列，建議用標題名稱
    route_col_idx = 23 # X欄
    route_col_name = df.columns[route_col_idx]
    
    # Mode B 還要考慮 AH 欄 (index 33)
    secondary_col_idx = 33
    
    final_rows = []
    headers = df.columns.tolist()
    final_rows.append(headers)
    
    # 取得所有不重複路線
    all_routes = set(df.iloc[:, route_col_idx].unique())
    if mode == 'B':
        all_routes.update(df.iloc[:, secondary_col_idx].unique())
    
    # 排序 (這裡可以加入你的自定義排序邏輯)
    sorted_routes = sorted([r for r in all_routes if r])
    
    for route in sorted_routes:
        # 篩選屬於該路線的資料
        # Mode A/C/D: 只看 X 欄
        # Mode B: 看 X 欄 OR AH 欄
        if mode == 'B':
            mask = (df.iloc[:, route_col_idx] == route) | (df.iloc[:, secondary_col_idx] == route)
        else:
            mask = (df.iloc[:, route_col_idx] == route)
            
        group = df[mask]
        
        if group.empty: continue
        
        # 標題列
        title_row = [''] * len(headers)
        title_row[0] = route
        final_rows.append(title_row)
        
        # 資料列
        final_rows.extend(group.values.tolist())
        
        # 總和列 (假設板數在第 18 欄, index 17)
        sum_val = pd.to_numeric(group.iloc[:, 17], errors='coerce').sum()
        sum_row = [''] * len(headers)
        sum_row[17] = f"總和: {sum_val}"
        final_rows.append(sum_row)
        
    try:
        ws_dst = sh.worksheet(dst_name)
        ws_dst.clear()
    except:
        ws_dst = sh.add_worksheet(dst_name, rows=len(final_rows)+100, cols=len(headers))
        
    ws_dst.update(final_rows)
    return f"[{mode}模態] 已建立 {dst_name}"

# --- 🚀 API 路由 ---
@app.route('/', methods=['GET'])
def home():
    return "物流 AI 系統運作中 (Full Modes)"

@app.route('/execute', methods=['POST'])
def execute():
    try:
        data = request.json
        action = data.get('action')
        mode = data.get('mode', 'A') # 預設 A
        
        sh = get_sh()
        msg = ""
        
        if action == 'step1':
            msg = step1_clear(sh)
        elif action == 'step2':
            msg = step2_filter(sh, mode)
        elif action == 'step3':
            msg = step3_mapping(sh, mode)
        elif action == 'step4':
            msg = step4_create(sh, mode)
        elif action == 'all':
            # 串聯執行
            m1 = step1_clear(sh) if mode == 'A' else "Skip Step1" # 通常只有 A 模態開頭要大掃除
            m2 = step2_filter(sh, mode)
            m3 = step3_mapping(sh, mode)
            m4 = step4_create(sh, mode)
            msg = f"[{mode}模態] 全部執行完畢"
            
        return jsonify({"status": "success", "message": msg})
        
    except Exception as e:
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
