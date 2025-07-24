import gspread
from google.oauth2.service_account import Credentials
import os
import json
from datetime import datetime

def get_google_sheets_client():
    """
    Setup and return Google Sheets client using service account credentials.
    """
    # Try to get credentials from environment variable first
    creds_json = os.environ.get('GOOGLE_SHEETS_CREDENTIALS_JSON')
    
    if creds_json:
        # Use environment variable
        creds_dict = json.loads(creds_json)
    else:
        # Try to load from google-credentials.json file
        creds_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'google-credentials.json')
        if os.path.exists(creds_file_path):
            with open(creds_file_path, 'r') as f:
                creds_dict = json.load(f)
        else:
            raise ValueError("No Google credentials found. Please set GOOGLE_SHEETS_CREDENTIALS_JSON environment variable or ensure google-credentials.json exists.")
    
    # Define scope for Google Sheets API
    scope = ['https://spreadsheets.google.com/feeds',
             'https://www.googleapis.com/auth/drive']
    
    # Create credentials
    credentials = Credentials.from_service_account_info(creds_dict, scopes=scope)
    
    # Create client
    client = gspread.authorize(credentials)
    return client

def get_items_data():
    """
    Get all items data from Google Sheets.
    Returns list of dictionaries with item data.
    """
    try:
        client = get_google_sheets_client()
        sheet_id = os.environ.get('ITEMS_SHEET_ID')
        if not sheet_id:
            raise ValueError("ITEMS_SHEET_ID environment variable not set")
        
        sheet = client.open_by_key(sheet_id).sheet1
        records = sheet.get_all_records()
        
        # Convert to expected format
        items = []
        for record in records:
            if record.get('Item Name') or record.get('Item'):  # Handle different column names
                item_name = record.get('Item Name', record.get('Item', ''))
                weight = record.get('Weight (lbs)', record.get('Weight', 0))
                items.append({
                    'Item': item_name,
                    'Weight': weight
                })
        return items
    except Exception as e:
        print(f"Error getting items data: {e}")
        return []

def get_item_names():
    """
    Get list of item names from Google Sheets.
    Returns list of item names.
    """
    items = get_items_data()
    return [item['Item'] for item in items if item['Item']]

def get_item_weight(item_name):
    """
    Get weight for a specific item from Google Sheets.
    Returns weight as float or None if not found.
    """
    items = get_items_data()
    item_name_lower = item_name.lower().strip()
    
    for item in items:
        if item['Item'].lower().strip() == item_name_lower:
            return float(item['Weight'])
    return None

def add_item_to_sheet(name, weight):
    """
    Add a new item to the Google Sheets items list.
    """
    try:
        client = get_google_sheets_client()
        sheet_id = os.environ.get('ITEMS_SHEET_ID')
        sheet = client.open_by_key(sheet_id).sheet1
        
        # Check if item already exists
        existing_items = get_item_names()
        if name.lower().strip() in [item.lower().strip() for item in existing_items]:
            raise ValueError("Item already exists")
        
        # Add new row
        sheet.append_row([name, weight])
        return True
    except Exception as e:
        print(f"Error adding item: {e}")
        raise e

def remove_item_from_sheet(name):
    """
    Remove an item from the Google Sheets items list.
    """
    try:
        client = get_google_sheets_client()
        sheet_id = os.environ.get('ITEMS_SHEET_ID')
        sheet = client.open_by_key(sheet_id).sheet1
        
        # Find and delete the row
        all_values = sheet.get_all_values()
        for i, row in enumerate(all_values):
            if row and row[0].lower().strip() == name.lower().strip():
                sheet.delete_rows(i + 1)  # Sheets are 1-indexed
                return True
        return False
    except Exception as e:
        print(f"Error removing item: {e}")
        raise e

def save_shipping_history(item_name, per_unit_cost, per_unit_cost_offset, quantity=1, vendor=None, is_ups='Yes', weight_used=''):
    """
    Save shipping history to Google Sheets, including quantity, vendor, UPS flag, and weight used.
    """
    try:
        client = get_google_sheets_client()
        sheet_id = os.environ.get('HISTORY_SHEET_ID')
        if not sheet_id:
            raise ValueError("HISTORY_SHEET_ID environment variable not set")
        sheet = client.open_by_key(sheet_id).sheet1
        # Create timestamp
        timestamp = datetime.now().isoformat(sep=' ', timespec='seconds')
        # Add new row (add vendor, UPS, and weight used as last columns)
        row = [item_name, per_unit_cost, per_unit_cost_offset, timestamp, quantity, vendor or "", is_ups, weight_used]
        sheet.append_row(row)
        return True
    except Exception as e:
        print(f"Error saving shipping history: {e}")
        return False

def get_shipping_history():
    """
    Get all shipping history from Google Sheets.
    Returns list of dictionaries with history data, including vendor and UPS.
    """
    try:
        client = get_google_sheets_client()
        sheet_id = os.environ.get('HISTORY_SHEET_ID')
        if not sheet_id:
            return []
        sheet = client.open_by_key(sheet_id).sheet1
        records = sheet.get_all_records()
        # Convert to expected format
        history = []
        for record in records:
            if record.get('Item Name'):
                history.append({
                    'Item Name': record['Item Name'],
                    'Per-Unit Shipping Cost': record.get('Per-Unit Shipping Cost', 0),
                    'Per-Unit Shipping Cost (Offset)': record.get('Per-Unit Shipping Cost (Offset)', 0),
                    'Timestamp': record.get('Timestamp', ''),
                    'Quantity': record.get('Quantity', 1),
                    'Vendor': record.get('Vendor', ''),
                    'UPS': record.get('UPS', ''),
                    'Weight Used': record.get('Weight Used', '')
                })
        return history
    except Exception as e:
        print(f"Error getting shipping history: {e}")
        return []

def delete_item_shipping_history(item_name, vendor=None):
    """
    Delete all shipping history for a specific item and vendor (if provided).
    """
    try:
        client = get_google_sheets_client()
        sheet_id = os.environ.get('HISTORY_SHEET_ID')
        if not sheet_id:
            return True
        sheet = client.open_by_key(sheet_id).sheet1
        # Find and delete rows
        all_values = sheet.get_all_values()
        rows_to_delete = []
        for i, row in enumerate(all_values):
            if row and row[0].lower().strip() == item_name.lower().strip():
                if vendor is None or (len(row) > 5 and row[5].lower().strip() == vendor.lower().strip()):
                    rows_to_delete.append(i + 1)  # Sheets are 1-indexed
        # Delete rows in reverse order to maintain indices
        for row_num in reversed(rows_to_delete):
            sheet.delete_rows(row_num)
        return True
    except Exception as e:
        print(f"Error deleting shipping history: {e}")
        return False 

def get_items_with_weights():
    """
    Return all items with their weights as a list of dicts: {"name": ..., "weight": ...}
    """
    items = get_items_data()
    return [{"name": item["Item"], "weight": item["Weight"]} for item in items if item["Item"]] 

def get_last_weight_used(item_name, vendor=None):
    """
    Return the most recent weight used for an item (optionally filtered by vendor) from the historic data sheet.
    """
    history = get_shipping_history()
    filtered = [r for r in history if r.get('Item Name', '').strip().lower() == item_name.strip().lower()]
    if vendor:
        filtered = [r for r in filtered if r.get('Vendor', '').strip().lower() == vendor.strip().lower()]
    # Sort by timestamp descending
    filtered.sort(key=lambda r: r.get('Timestamp', ''), reverse=True)
    for record in filtered:
        weight = record.get('Weight Used', '')
        if weight not in (None, '', 'N/A'):
            try:
                return float(weight)
            except Exception:
                continue
    return None 