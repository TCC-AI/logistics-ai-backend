# main.py
from flask import Flask, request, jsonify
from google.oauth2.service_account import Credentials
import gspread
import logging
from datetime import datetime
from typing import Dict, List, Optional, Tuple
import time

# 配置日誌
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = Flask(__name__)

# Google Sheets 配置
SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
SPREADSHEET_ID = 'YOUR_SPREADSHEET_ID'  # 需要配置

class GoogleSheetsService:
    """Google Sheets 服務類"""
    
    def __init__(self, credentials_path: str, spreadsheet_id: str):
        self.credentials = Credentials.from_service_account_file(
            credentials_path, scopes=SCOPES
        )
        self.client = gspread.authorize(self.credentials)
        self.spreadsheet = self.client.open_by_key(spreadsheet_id)
    
    def get_sheet(self, sheet_name: str):
        """獲取工作表"""
        try:
            return self.spreadsheet.worksheet(sheet_name)
        except gspread.exceptions.WorksheetNotFound:
            return None
    
    def create_sheet(self, sheet_name: str):
        """創建新工作表"""
        try:
            return self.spreadsheet.add_worksheet(title=sheet_name, rows=1000, cols=50)
        except Exception as e:
            logger.error(f"創建工作表失敗: {e}")
            return None
    
    def delete_sheet(self, sheet_name: str):
        """刪除工作表"""
        try:
            sheet = self.get_sheet(sheet_name)
            if sheet:
                self.spreadsheet.del_worksheet(sheet)
                return True
        except Exception as e:
            logger.error(f"刪除工作表失敗: {e}")
        return False
    
    def clear_sheet(self, sheet_name: str):
        """清空工作表"""
        sheet = self.get_sheet(sheet_name)
        if sheet:
            sheet.clear()
            return True
        return False

class ProcessStatus:
    """流程狀態管理"""
    
    def __init__(self, sheets_service: GoogleSheetsService):
        self.sheets_service = sheets_service
        self.status_sheet_name = '🔄執行狀態'
    
    def set_status(self, status: str):
        """設置狀態"""
        try:
            sheet = self.sheets_service.get_sheet(self.status_sheet_name)
            if not sheet:
                sheet = self.sheets_service.create_sheet(self.status_sheet_name)
                sheet.update('A1:C1', [['狀態', '時間戳記', '詳細信息']])
            
            timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
            sheet.update('A2:C2', [[status, timestamp, f'狀態更新於 {timestamp}']])
            logger.info(f"📊 狀態已更新: {status} at {timestamp}")
        except Exception as e:
            logger.error(f"設置狀態失敗: {e}")
    
    def get_status(self) -> str:
        """獲取狀態"""
        try:
            sheet = self.sheets_service.get_sheet(self.status_sheet_name)
            if not sheet:
                return 'NOT_STARTED'
            
            value = sheet.acell('A2').value
            return value if value else 'NOT_STARTED'
        except Exception as e:
            logger.error(f"獲取狀態失敗: {e}")
            return 'ERROR'
    
    def clear_status(self):
        """清除狀態"""
        try:
            self.sheets_service.delete_sheet(self.status_sheet_name)
            logger.info('🧹 狀態已清除')
        except Exception as e:
            logger.error(f"清除狀態失敗: {e}")

class WorkspaceManager:
    """工作區管理"""
    
    def __init__(self, sheets_service: GoogleSheetsService):
        self.sheets_service = sheets_service
        self.keep_sheets = {
            '託收託運回報', 'GAI每日訂單分析', '5678月貨主收送點參照', 
            '5678月班別路線參照', '參照', '碳排', '配送地址參照', '低碳路線表',
            '託收託運回報_篩選(B)', '當日各路線表(B)', '各路線板數(B)',
            '託收託運回報_篩選(C)', '當日各路線表(C)', '各路線板數(C)',
            '退貨表', '託收托運點資訊', '託收托運點資訊(簡)', '指定日期',
            '託收託運回報_篩選', '當日各路線表', '各路線板數', '路線表'
        }
    
    def clear_workspace(self, mode: str = 'A'):
        """清除桌面"""
        try:
            all_sheets = self.sheets_service.spreadsheet.worksheets()
            
            for sheet in all_sheets:
                sheet_name = sheet.title
                
                # 根據模式決定要刪除的工作表
                if mode == 'D':
                    if '(D)' in sheet_name or sheet_name == '託收託運回報_篩選(D)':
                        self.sheets_service.delete_sheet(sheet_name)
                else:
                    if sheet_name not in self.keep_sheets:
                        self.sheets_service.delete_sheet(sheet_name)
            
            logger.info(f"✅ 清除桌面完成 (模式: {mode})")
            return True
        except Exception as e:
            logger.error(f"清除桌面失敗: {e}")
            return False

class DateFilter:
    """日期篩選器"""
    
    def __init__(self, sheets_service: GoogleSheetsService):
        self.sheets_service = sheets_service
    
    def format_date_for_comparison(self, date_value) -> str:
        """格式化日期用於比較"""
        if not date_value:
            return ''
        
        try:
            if isinstance(date_value, datetime):
                date_obj = date_value
            elif isinstance(date_value, str):
                date_obj = datetime.strptime(date_value, '%Y-%m-%d')
            else:
                return ''
            
            return date_obj.strftime('%Y-%m-%d')
        except Exception as e:
            logger.error(f"日期格式化失敗: {e}")
            return ''
    
    def filter_data_by_date(self, mode: str = 'A') -> bool:
        """日期篩選"""
        try:
            # 獲取指定日期
            date_sheet = self.sheets_service.get_sheet('指定日期')
            if not date_sheet:
                logger.error("找不到「指定日期」工作表")
                return False
            
            # 根據模式選擇日期單元格
            cell_ref = 'A3' if mode == 'D' else 'A2'
            target_date = date_sheet.acell(cell_ref).value
            
            if not target_date:
                logger.error("未設定目標日期")
                return False
            
            target_date_str = self.format_date_for_comparison(target_date)
            
            # 獲取原始數據
            report_sheet = self.sheets_service.get_sheet('託收託運回報')
            if not report_sheet:
                logger.error("找不到「託收託運回報」工作表")
                return False
            
            all_data = report_sheet.get_all_values()
            if len(all_data) < 2:
                logger.error("數據不足")
                return False
            
            # 篩選數據
            headers = all_data[0]
            filtered_data = [headers]
            
            for row in all_data[1:]:
                if len(row) > 5:
                    f_column_value = row[5]  # F欄
                    f_date_str = self.format_date_for_comparison(f_column_value)
                    
                    if f_date_str == target_date_str:
                        filtered_data.append(row)
            
            if len(filtered_data) <= 1:
                logger.error("沒有符合條件的數據")
                return False
            
            # 寫入篩選結果
            sheet_name = f'託收託運回報_篩選({mode})' if mode != 'A' else '託收託運回報_篩選'
            filtered_sheet = self.sheets_service.get_sheet(sheet_name)
            
            if not filtered_sheet:
                filtered_sheet = self.sheets_service.create_sheet(sheet_name)
            else:
                filtered_sheet.clear()
            
            # 批量寫入數據
            if filtered_data:
                filtered_sheet.update(f'A1:Z{len(filtered_data)}', filtered_data)
            
            logger.info(f"✅ 日期篩選完成，共 {len(filtered_data)-1} 筆數據")
            return True
            
        except Exception as e:
            logger.error(f"日期篩選失敗: {e}")
            return False

class RouteMapper:
    """路線比對器"""
    
    def __init__(self, sheets_service: GoogleSheetsService):
        self.sheets_service = sheets_service
    
    def preprocess_h_value(self, h_value: str) -> str:
        """預處理 H 欄值"""
        if not h_value:
            return ''
        h_string = str(h_value).strip()
        return h_string[3:] if h_string.startswith('(預)') else h_string
    
    def parse_percentage_strings(self, value: str) -> List[Dict]:
        """解析百分比字串"""
        if not value or value == '無':
            return []
        
        import re
        matches = re.finditer(r'(\d+)%([^\s]+)', value)
        return [
            {'percentage': int(match.group(1)), 'label': match.group(2)}
            for match in matches
        ]
    
    def format_label(self, option: Dict, c_value: str, d_value: str) -> str:
        """格式化標籤"""
        prefix = '早-' if option['label'] in c_value else '晚-'
        return prefix + option['label']
    
    def auto_route_mapping(self, mode: str = 'A') -> bool:
        """自動路線比對"""
        try:
            # 確定工作表名稱
            sheet_name = f'託收託運回報_篩選({mode})' if mode != 'A' else '託收託運回報_篩選'
            
            report_sheet = self.sheets_service.get_sheet(sheet_name)
            reference_sheet = self.sheets_service.get_sheet('5678月貨主收送點參照')
            
            if not report_sheet or not reference_sheet:
                logger.error("找不到必要的工作表")
                return False
            
            report_data = report_sheet.get_all_values()
            reference_data = reference_sheet.get_all_values()
            
            # 建立參照映射
            reference_map = {}
            for row in reference_data[1:]:
                if len(row) >= 4:
                    ref_owner = row[0]
                    ref_point = row[1]
                    if ref_owner and ref_point:
                        key = f"{ref_owner}|{ref_point}"
                        reference_map[key] = {
                            'c_value': row[2],
                            'd_value': row[3]
                        }
            
            # 處理路線比對
            route1_values = []
            route2_values = [] if mode == 'B' else None
            
            for row in report_data[1:]:
                if len(row) >= 8:
                    report_owner = row[4]  # E欄
                    h_value = row[7]  # H欄
                    report_point = self.preprocess_h_value(h_value)
                    key = f"{report_owner}|{report_point}"
                    
                    route_data = reference_map.get(key)
                    if route_data:
                        c_value = route_data['c_value']
                        d_value = route_data['d_value']
                        c_options = self.parse_percentage_strings(c_value)
                        d_options = self.parse_percentage_strings(d_value)
                        all_options = sorted(
                            c_options + d_options,
                            key=lambda x: x['percentage'],
                            reverse=True
                        )
                        
                        # 第一路線
                        route1_values.append([
                            self.format_label(all_options[0], c_value, d_value)
                            if all_options else ''
                        ])
                        
                        # B模態需要第二路線
                        if mode == 'B' and len(all_options) > 1 and all_options[1]['percentage'] > 40:
                            route2_values.append([
                                self.format_label(all_options[1], c_value, d_value)
                            ])
                        elif mode == 'B':
                            route2_values.append([''])
                    else:
                        route1_values.append([''])
                        if mode == 'B':
                            route2_values.append([''])
            
            # 寫入結果
            if route1_values:
                # X欄 (第24欄)
                report_sheet.update(f'X2:X{len(route1_values)+1}', route1_values)
                
                # 清空 AH 和 AI 欄
                empty_values = [['']] * len(route1_values)
                report_sheet.update(f'AH2:AH{len(route1_values)+1}', empty_values)
                report_sheet.update(f'AI2:AI{len(route1_values)+1}', empty_values)
                
                # B模態寫入第二路線到 AH 欄
                if mode == 'B' and route2_values:
                    report_sheet.update(f'AH2:AH{len(route2_values)+1}', route2_values)
            
            logger.info(f"✅ 路線比對完成 (模式: {mode})")
            return True
            
        except Exception as e:
            logger.error(f"路線比對失敗: {e}")
            return False

class RouteOrderManager:
    """路線順序管理"""
    
    @staticmethod
    def get_route_order() -> List[str]:
        """獲取路線順序"""
        routes = []
        for period in ['早', '晚']:
            for region in ['北', '中', '南']:
                for num in range(1, 19):
                    routes.append(f'{period}-{region}{"一二三四五六七八九十十一十二十三十四十五十六十七十八"[num-1] if num <= 10 else "十" + "一二三四五六七八"[num-11]}線')
        return routes

class SheetCreator:
    """工作表創建器"""
    
    def __init__(self, sheets_service: GoogleSheetsService):
        self.sheets_service = sheets_service
        self.route_order = RouteOrderManager.get_route_order()
    
    def create_sheets_by_route(self, mode: str = 'A') -> bool:
        """按路線創建工作表"""
        try:
            logger.info(f'🚀 開始創建路線工作表 (模式: {mode})')
            
            # 確定工作表名稱
            source_name = f'託收託運回報_篩選({mode})' if mode != 'A' else '託收託運回報_篩選'
            combined_name = f'當日各路線表({mode})' if mode != 'A' else '當日各路線表'
            summary_name = f'各路線板數({mode})' if mode != 'A' else '各路線板數'
            
            report_sheet = self.sheets_service.get_sheet(source_name)
            if not report_sheet:
                logger.error(f'找不到 {source_name} 工作表')
                return False
            
            # 讀取數據
            data = report_sheet.get_all_values()
            if len(data) < 2:
                logger.error('數據不足')
                return False
            
            # 準備合併工作表
            combined_sheet = self.sheets_service.get_sheet(combined_name)
            if not combined_sheet:
                combined_sheet = self.sheets_service.create_sheet(combined_name)
            else:
                combined_sheet.clear()
            
            # 分類數據
            route_data = {route: [] for route in self.route_order}
            unmatched_data = []
            route_order_set = set(self.route_order)
            
            for row in data[1:]:
                if len(row) > 23:
                    h_value = row[7]
                    # 根據模式選擇路線欄位
                    if mode == 'B':
                        primary_route = str(row[23]).strip().rstrip(',') if row[23] else ''
                        secondary_route = str(row[33]).strip().rstrip(',') if row[33] else ''
                    else:
                        primary_route = str(row[23]).strip().rstrip(',') if row[23] else ''
                        secondary_route = ''
                    
                    # 排除昶青
                    if h_value and '昶青' in str(h_value):
                        continue
                    
                    # 分配到路線
                    if primary_route and primary_route in route_order_set:
                        route_data[primary_route].append(row)
                    
                    if mode == 'B' and secondary_route and secondary_route in route_order_set:
                        if secondary_route != primary_route:
                            route_data[secondary_route].append(row)
                    
                    if not primary_route and not secondary_route:
                        unmatched_data.append(row)
            
            # 構建合併數據
            combined_data = [data[0]]  # 標題行
            
            for route in self.route_order:
                route_rows = route_data[route]
                if route_rows:
                    # 空行
                    combined_data.append([''] * len(data[0]))
                    # 路線標題
                    title_row = [''] * len(data[0])
                    title_row[0] = route
                    combined_data.append(title_row)
                    # 路線數據
                    combined_data.extend(route_rows)
                    # 總和行
                    sum_p = sum(
                        float(row[17]) if row[17] and str(row[17]).replace('.', '').isdigit() else 0
                        for row in route_rows
                    )
                    sum_row = [''] * len(data[0])
                    sum_row[17] = f'總和: {sum_p}'
                    combined_data.append(sum_row)
            
            # 未匹配數據
            if unmatched_data:
                combined_data.append([''] * len(data[0]))
                title_row = [''] * len(data[0])
                title_row[0] = '尚未排派路線'
                combined_data.append(title_row)
                combined_data.extend(unmatched_data)
                sum_p = sum(
                    float(row[17]) if row[17] and str(row[17]).replace('.', '').isdigit() else 0
                    for row in unmatched_data
                )
                sum_row = [''] * len(data[0])
                sum_row[17] = f'總和: {sum_p}'
                combined_data.append(sum_row)
            
            # 寫入合併數據
            if combined_data:
                combined_sheet.update(f'A1:Z{len(combined_data)}', combined_data)
            
            # 創建摘要
            self._create_summary(summary_name, route_data)
            
            logger.info(f'✅ 路線工作表創建完成 (模式: {mode})')
            return True
            
        except Exception as e:
            logger.error(f'路線工作表創建失敗: {e}')
            return False
    
    def _create_summary(self, summary_name: str, route_data: Dict):
        """創建摘要工作表"""
        try:
            summary_sheet = self.sheets_service.get_sheet(summary_name)
            if not summary_sheet:
                summary_sheet = self.sheets_service.create_sheet(summary_name)
            else:
                summary_sheet.clear()
            
            summary_data = [['路線名稱', '板數總和', '取貨', '配送']]
            
            for route in self.route_order:
                rows = route_data.get(route, [])
                if not rows:
                    continue
                
                total_boards = 0
                pickup_map = {}
                delivery_map = {}
                
                for row in rows:
                    if len(row) > 17:
                        customer_name = row[7]
                        service_type = row[6]
                        board_count = float(row[17]) if row[17] and str(row[17]).replace('.', '').isdigit() else 0
                        
                        if not customer_name:
                            continue
                        
                        total_boards += board_count
                        
                        if service_type == '取貨':
                            pickup_map[customer_name] = pickup_map.get(customer_name, 0) + board_count
                        elif service_type == '配送':
                            delivery_map[customer_name] = delivery_map.get(customer_name, 0) + board_count
                
                pickup_string = ', '.join([f'{name} ({total})' for name, total in pickup_map.items()])
                delivery_string = ', '.join([f'{name} ({total})' for name, total in delivery_map.items()])
                
                summary_data.append([route, total_boards, pickup_string, delivery_string])
            
            if len(summary_data) > 1:
                summary_sheet.update(f'A1:D{len(summary_data)}', summary_data)
            
            logger.info('✅ 摘要工作表創建完成')
            
        except Exception as e:
            logger.error(f'摘要創建失敗: {e}')

# API 路由
@app.route('/api/step1', methods=['POST'])
def step1_clear_workspace():
    """步驟1：清除桌面"""
    try:
        data = request.json
        mode = data.get('mode', 'A')
        
        sheets_service = GoogleSheetsService('credentials.json', SPREADSHEET_ID)
        status_manager = ProcessStatus(sheets_service)
        workspace_manager = WorkspaceManager(sheets_service)
        
        status_manager.set_status('STEP1_RUNNING')
        
        result = workspace_manager.clear_workspace(mode)
        
        if result:
            status_manager.set_status('STEP1_COMPLETED')
            return jsonify({'success': True, 'message': '步驟1：清除桌面完成'})
        else:
            status_manager.set_status('STEP1_FAILED')
            return jsonify({'success': False, 'message': '步驟1失敗'}), 500
            
    except Exception as e:
        logger.error(f"步驟1失敗: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/step2', methods=['POST'])
def step2_filter_data():
    """步驟2：日期篩選"""
    try:
        data = request.json
        mode = data.get('mode', 'A')
        
        sheets_service = GoogleSheetsService('credentials.json', SPREADSHEET_ID)
        status_manager = ProcessStatus(sheets_service)
        date_filter = DateFilter(sheets_service)
        
        status_manager.set_status('STEP2_RUNNING')
        
        result = date_filter.filter_data_by_date(mode)
        
        if result:
            status_manager.set_status('STEP2_COMPLETED')
            return jsonify({'success': True, 'message': '步驟2：日期篩選完成'})
        else:
            status_manager.set_status('STEP2_FAILED')
            return jsonify({'success': False, 'message': '步驟2失敗'}), 500
            
    except Exception as e:
        logger.error(f"步驟2失敗: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/step3', methods=['POST'])
def step3_route_mapping():
    """步驟3：路線比對"""
    try:
        data = request.json
        mode = data.get('mode', 'A')
        
        sheets_service = GoogleSheetsService('credentials.json', SPREADSHEET_ID)
        status_manager = ProcessStatus(sheets_service)
        route_mapper = RouteMapper(sheets_service)
        
        status_manager.set_status('STEP3_RUNNING')
        
        result = route_mapper.auto_route_mapping(mode)
        
        if result:
            status_manager.set_status('STEP3_COMPLETED')
            return jsonify({'success': True, 'message': '步驟3：路線比對完成'})
        else:
            status_manager.set_status('STEP3_FAILED')
            return jsonify({'success': False, 'message': '步驟3失敗'}), 500
            
    except Exception as e:
        logger.error(f"步驟3失敗: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/step4', methods=['POST'])
def step4_create_sheets():
    """步驟4：創建工作表"""
    try:
        data = request.json
        mode = data.get('mode', 'A')
        
        sheets_service = GoogleSheetsService('credentials.json', SPREADSHEET_ID)
        status_manager = ProcessStatus(sheets_service)
        sheet_creator = SheetCreator(sheets_service)
        
        status_manager.set_status('STEP4_RUNNING')
        
        result = sheet_creator.create_sheets_by_route(mode)
        
        if result:
            status_manager.set_status('STEP4_COMPLETED')
            return jsonify({'success': True, 'message': '步驟4：創建工作表完成'})
        else:
            status_manager.set_status('STEP4_FAILED')
            return jsonify({'success': False, 'message': '步驟4失敗'}), 500
            
    except Exception as e:
        logger.error(f"步驟4失敗: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/execute_all', methods=['POST'])
def execute_all_steps():
    """執行所有步驟"""
    try:
        data = request.json
        mode = data.get('mode', 'A')
        
        sheets_service = GoogleSheetsService('credentials.json', SPREADSHEET_ID)
        status_manager = ProcessStatus(sheets_service)
        
        status_manager.set_status('ALL_STEPS_RUNNING')
        
        # 步驟1
        workspace_manager = WorkspaceManager(sheets_service)
        if not workspace_manager.clear_workspace(mode):
            raise Exception("步驟1失敗")
        time.sleep(2)
        
        # 步驟2
        date_filter = DateFilter(sheets_service)
        if not date_filter.filter_data_by_date(mode):
            raise Exception("步驟2失敗")
        time.sleep(2)
        
        # 步驟3
        route_mapper = RouteMapper(sheets_service)
        if not route_mapper.auto_route_mapping(mode):
            raise Exception("步驟3失敗")
        time.sleep(2)
        
        # 步驟4
        sheet_creator = SheetCreator(sheets_service)
        if not sheet_creator.create_sheets_by_route(mode):
            raise Exception("步驟4失敗")
        
        status_manager.set_status('ALL_STEPS_COMPLETED')
        return jsonify({'success': True, 'message': f'所有步驟完成 (模式: {mode})'})
        
    except Exception as e:
        logger.error(f"執行失敗: {e}")
        status_manager.set_status('ALL_STEPS_FAILED')
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/status', methods=['GET'])
def get_status():
    """獲取執行狀態"""
    try:
        sheets_service = GoogleSheetsService('credentials.json', SPREADSHEET_ID)
        status_manager = ProcessStatus(sheets_service)
        
        status = status_manager.get_status()
        return jsonify({'status': status})
        
    except Exception as e:
        logger.error(f"獲取狀態失敗: {e}")
        return jsonify({'status': 'ERROR', 'message': str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    """健康檢查"""
    return jsonify({'status': 'healthy'})

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5000)
