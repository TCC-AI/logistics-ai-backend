import os
import json
import re
import pandas as pd
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
    """解析 '60%早-北一, 40%晚-北一' 格式"""
    if not value_str or pd.isna(value_str):
        return []
    matches = re.findall(r'(\d+)%([^\s,]+)', str(value_str))
    options = []
    for pct, label in matches:
        options.append({'percentage': int(pct), 'label': label})
    # 依照百分比由大到小排序
    options.sort(key=lambda x: x['percentage'], reverse=True)
    return options

def format_date(val):
    """將各種日期格式統一轉為 YYYY-MM-DD"""
    if pd.isna(val) or val == '': return ''
    try:
        return pd.to_datetime(val).strftime('%Y-%m-%d')
    except:
        return str(val)

# --- 🧹 步驟 1: 清除桌面 (對應 clearWorkspace/DMode) ---
def step1_clear(sh, mode='A'):
    # 定義保留名單 (集合運算比陣列快)
    keep_sheets = {
        '託收託運回報', 'GAI每日訂單分析', '5678月貨主收送點參照', '5678月班別路線參照', 
        '參照', '碳排', '配送地址參照', '低碳路線表', '退貨表', '託收托運點資訊', 
        '託收托運點資訊(簡)', '指定日期'
    }
    
    # 如果是 Mode D，保留原本 A/B/C 模態產生的表，只刪除 (D) 相關
    # 根據你的 GAS 邏輯：D 模態只刪除含 (D) 的表
    
    worksheets = sh.worksheets()
    deleted_count = 0
    
    for ws in worksheets:
        name = ws.title
        should_delete = False
        
        if mode == 'D':
            # D 模態邏輯：只刪除名字包含 (D) 的
            if '(D)' in name or name == '託收託運回報_篩選(D)':
                should_delete = True
        else:
            # A/B/C 模態邏輯：保留名單以外的都刪除
            # 但為了安全，我們通常只刪除生成的表 (篩選、路線表、板數)
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

# --- 📅 步驟 2: 日期篩選 (對應 filterDataByDate/DMode) ---
def step2_filter(sh, mode='A'):
    # 設定後綴
    suffix = ""
    if mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    target_sheet_name = f'託收託運回報_篩選{suffix}'
    
    # 1. 讀取日期
    ws_date = sh.worksheet('指定日期')
    # D模態讀 A3, 其他讀 A2
    date_cell = 'A3' if mode == 'D' else 'A2'
    target_date_val = ws_date.acell(date_cell).value
    target_date = format_date(target_date_val)
    
    if not target_date:
        return f"錯誤：指定日期工作表 ({date_cell}) 未設定日期"

    # 2. 讀取原始資料
    ws_source = sh.worksheet('託收託運回報')
    data = ws_source.get_all_values()
    headers = data[0]
    df = pd.DataFrame(data[1:], columns=headers)
    
    # 3. 篩選
    # 假設日期在第 6 欄 (index 5)
    # 先將該欄位統一轉字串並格式化，再進行比對
    date_col_idx = 5
    
    # 這裡使用 apply 來處理日期格式，確保與 target_date 格式一致
    df['fmt_date'] = df.iloc[:, date_col_idx].apply(format_date)
    filtered_df = df[df['fmt_date'] == target_date].drop(columns=['fmt_date'])
    
    if filtered_df.empty:
        return f"錯誤：找不到日期 {target_date} 的資料"

    # 4. 寫入結果
    try:
        ws_target = sh.worksheet(target_sheet_name)
        ws_target.clear()
    except:
        ws_target = sh.add_worksheet(target_sheet_name, rows=1000, cols=len(headers)+10)
    
    # 準備寫入資料
    update_data = [filtered_df.columns.values.tolist()] + filtered_df.values.tolist()
    ws_target.update(update_data)
    
    # 設定置中 (選擇性，會稍微增加時間)
    # fmt = CellFormat(horizontalAlignment='CENTER')
    # format_cell_range(ws_target, 'A:Z', fmt)
    
    return f"[{mode}模態] 篩選完成，共 {len(filtered_df)} 筆"

# --- 🛣️ 步驟 3: 路線比對 + APP3 (對應 autoRouteMapping + APP3) ---
def step3_mapping(sh, mode='A'):
    suffix = ""
    if mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    sheet_name = f'託收託運回報_篩選{suffix}'
    ws = sh.worksheet(sheet_name)
    
    # 讀取資料
    df = pd.DataFrame(ws.get_all_records())
    
    # 讀取參照表 (5678月貨主收送點參照)
    ws_ref = sh.worksheet('5678月貨主收送點參照')
    df_ref = pd.DataFrame(ws_ref.get_all_records())
    
    # 建立映射字典
    # 假設參照表欄位: 貨主(0), 收送點(1), C欄(2), D欄(3)
    ref_cols = df_ref.columns
    mapping = {}
    for _, row in df_ref.iterrows():
        key = f"{str(row[ref_cols[0]]).strip()}|{str(row[ref_cols[1]]).strip()}"
        mapping[key] = {'C': str(row[ref_cols[2]]), 'D': str(row[ref_cols[3]])}
    
    # 準備 APP3 的映射表 (參照)
    ws_code_ref = sh.worksheet('參照')
    df_code = pd.DataFrame(ws_code_ref.get_all_records())
    code_cols = df_code.columns
    # 建立 A->B, C->D, E->F 的字典
    map_ab = dict(zip(df_code[code_cols[0]].astype(str).str.strip(), df_code[code_cols[1]]))
    map_cd = dict(zip(df_code[code_cols[2]].astype(str).str.strip(), df_code[code_cols[3]]))
    map_ef = dict(zip(df_code[code_cols[4]].astype(str).str.strip(), df_code[code_cols[5]]))

    # 開始處理每一列
    # 假設回報表欄位: 貨主(4), H欄(7)
    rep_cols = df.columns
    col_owner = rep_cols[4] # index 4
    col_h = rep_cols[7]     # index 7
    
    x_values = []  # 主路線 (第24欄)
    ah_values = [] # 副路線 (第34欄, 僅 Mode B)
    
    # APP3 結果
    ac_values = [] # 前5字
    aa_values = [] # 第1字
    ab_values = [] # 第3字
    
    for _, row in df.iterrows():
        owner = str(row[col_owner]).strip()
        # 處理 H 欄：去除 (預)
        h_val = str(row[col_h]).strip()
        if h_val.startswith('(預)'): h_val = h_val[3:]
        
        key = f"{owner}|{h_val}"
        
        primary_route = ''
        secondary_route = ''
        
        # --- 路線比對邏輯 ---
        if key in mapping:
            ref_data = mapping[key]
            opts_c = parse_route_options(ref_data['C'])
            opts_d = parse_route_options(ref_data['D'])
            all_opts = sorted(opts_c + opts_d, key=lambda x: x['percentage'], reverse=True)
            
            if all_opts:
                primary_route = all_opts[0]['label']
                # Mode B 邏輯：第二選項 > 40% 填入 AH
                if mode == 'B' and len(all_opts) > 1 and all_opts[1]['percentage'] > 40:
                    secondary_route = all_opts[1]['label']
        
        x_values.append(primary_route)
        ah_values.append(secondary_route)
        
        # --- APP3 代碼映射邏輯 (針對 primary_route / X欄) ---
        x_str = str(primary_route).strip()
        
        # AC (前5字) -> Map A->B
        val_ac = map_ab.get(x_str[:5], '') if len(x_str) >= 5 else ''
        ac_values.append(val_ac)
        
        # AA (第1字) -> Map C->D
        val_aa = map_cd.get(x_str[0], '') if len(x_str) >= 1 else ''
        aa_values.append(val_aa)
        
        # AB (第3字) -> Map E->F
        val_ab = map_ef.get(x_str[2], '') if len(x_str) >= 3 else ''
        ab_values.append(val_ab)

    # --- 批次寫入 ---
    # X 欄 (第24欄)
    ws.update('X2', [[x] for x in x_values])
    
    # AH 欄 (第34欄) - 僅 Mode B 寫入，其他清空
    if mode == 'B':
        ws.update('AH2', [[x] for x in ah_values])
    else:
        # 為了效能，如果不是 Mode B，可以選擇不清空或寫入空值
        # 這裡依照 GAS 邏輯：清空
        empty_col = [[''] for _ in range(len(x_values))]
        ws.update('AH2', empty_col)
        
    # AI 欄 (第35欄) - GAS 邏輯是清空
    ws.update('AI2', [[''] for _ in range(len(x_values))])
    
    # APP3 寫入: AA(27), AB(28), AC(29)
    ws.update('AA2', [[x] for x in aa_values])
    ws.update('AB2', [[x] for x in ab_values])
    ws.update('AC2', [[x] for x in ac_values])
    
    return f"[{mode}模態] 路線比對 & APP3 映射完成"

# --- 📊 步驟 4: 創建工作表 (對應 createSheetsByRoute) ---
def step4_create(sh, mode='A'):
    suffix = ""
    if mode == 'B': suffix = "(B)"
    elif mode == 'C': suffix = "(C)"
    elif mode == 'D': suffix = "(D)"
    
    # 來源表：Mode B 來源也是無後綴的篩選表，其他則是對應後綴
    src_name = f'託收託運回報_篩選{suffix if mode != "B" else ""}'
    dst_name = f'當日各路線表{suffix}'
    summary_name = f'各路線板數{suffix}'
    
    ws_src = sh.worksheet(src_name)
    df = pd.DataFrame(ws_src.get_all_records())
    
    # 排除 '昶青' (GAS 邏輯)
    # 假設 H 欄是 index 7
    col_h_name = df.columns[7]
    df = df[~df[col_h_name].astype(str).str.contains('昶青', na=False)]
    
    # 取得 X 欄 (index 23) 和 AH 欄 (index 33)
    col_x = df.columns[23]
    col_ah = df.columns[33]
    
    # 取得所有路線名稱並排序
    routes = set(df[col_x].dropna().unique())
    if mode == 'B':
        routes.update(df[col_ah].dropna().unique())
    
    # 移除空值
    routes = sorted([r for r in routes if r and str(r).strip() != ''])
    
    # 準備輸出資料
    final_rows = []
    headers = df.columns.tolist()
    final_rows.append(headers)
    
    # 準備摘要資料
    summary_rows = [['路線名稱', '板數總和', '取貨', '配送']]
    
    for route in routes:
        # 篩選該路線資料
        if mode == 'B':
            mask = (df[col_x] == route) | (df[col_ah] == route)
        else:
            mask = (df[col_x] == route)
            
        group = df[mask]
        if group.empty: continue
        
        # --- 構建主表資料 ---
        # 標題列
        title_row = [''] * len(headers)
        title_row[0] = route
        final_rows.append(title_row)
        
        # 資料內容
        final_rows.extend(group.values.tolist())
        
        # 總和列 (假設板數在 index 17)
        col_board_idx = 17
        total_boards = pd.to_numeric(group.iloc[:, col_board_idx], errors='coerce').fillna(0).sum()
        sum_row = [''] * len(headers)
        sum_row[col_board_idx] = f"總和: {total_boards}"
        final_rows.append(sum_row)
        
        # --- 構建摘要資料 ---
        # 統計取貨/配送 (假設服務類型在 index 6, 客戶名在 index 7)
        col_type_idx = 6
        col_cust_idx = 7
        
        pickup_map = {}
        delivery_map = {}
        
        for _, row in group.iterrows():
            ctype = str(row.iloc[col_type_idx])
            cust = str(row.iloc[col_cust_idx])
            boards = pd.to_numeric(row.iloc[col_board_idx], errors='coerce') or 0
            
            if ctype == '取貨':
                pickup_map[cust] = pickup_map.get(cust, 0) + boards
            elif ctype == '配送':
                delivery_map[cust] = delivery_map.get(cust, 0) + boards
                
        pickup_str = ", ".join([f"{k} ({v})" for k, v in pickup_map.items()])
        delivery_str = ", ".join([f"{k} ({v})" for k, v in delivery_map.items()])
        
        summary_rows.append([route, total_boards, pickup_str, delivery_str])
        
    # --- 寫入主表 ---
    try:
        ws_dst = sh.worksheet(dst_name)
        ws_dst.clear()
    except:
        ws_dst = sh.add_worksheet(dst_name, rows=len(final_rows)+100, cols=len(headers))
    ws_dst.update(final_rows)
    
    # 簡單格式化 (標題列綠色)
    # 這裡為了速度先略過詳細格式化，Python 寫入資料才是重點
    
    # --- 寫入摘要表 ---
    try:
        ws_sum = sh.worksheet(summary_name)
        ws_sum.clear()
    except:
        ws_sum = sh.add_worksheet(summary_name, rows=len(summary_rows)+20, cols=5)
    ws_sum.update(summary_rows)
    
    return f"[{mode}模態] 已建立 {dst_name} 與 {summary_name}"

# --- 🚀 API 路由 ---
@app.route('/', methods=['GET'])
def home():
    return "物流 AI 系統運作中 (Full Logic)"

@app.route('/execute', methods=['POST'])
def execute():
    try:
        data = request.json
        action = data.get('action')
        mode = data.get('mode', 'A')
        
        sh = get_sh()
        msg = ""
        
        if action == 'step1':
            msg = step1_clear(sh, mode)
        elif action == 'step2':
            msg = step2_filter(sh, mode)
        elif action == 'step3':
            msg = step3_mapping(sh, mode)
        elif action == 'step4':
            msg = step4_create(sh, mode)
        elif action == 'all':
            # 串聯執行
            msgs = []
            if mode == 'A': msgs.append(step1_clear(sh, mode))
            msgs.append(step2_filter(sh, mode))
            msgs.append(step3_mapping(sh, mode))
            msgs.append(step4_create(sh, mode))
            msg = " -> ".join(msgs)
            
        return jsonify({"status": "success", "message": msg})
        
    except Exception as e:
        # 印出錯誤到 Render Logs 方便除錯
        print(f"Error: {str(e)}")
        return jsonify({"status": "error", "message": str(e)}), 500

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=10000)
