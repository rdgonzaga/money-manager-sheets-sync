import os
import sqlite3
import pandas as pd
import gspread
from dotenv import load_dotenv
import time

load_dotenv()
DB_PATH = os.getenv("MM_DB_PATH")
GOOGLE_SERVICE_ACCOUNT_FILE = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE")
GOOGLE_SHEETS_SPREADSHEET_ID = os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID")
STATE_FILE = os.path.join("data", ".sync_state")
SYNTHETIC_CATEGORIES = {"Transfer", "Uncategorized"}

def validate_environment():
    errors = []

    if not DB_PATH:
        errors.append("MM_DB_PATH not found in .env file")

    if not GOOGLE_SERVICE_ACCOUNT_FILE:
        errors.append("GOOGLE_SERVICE_ACCOUNT_FILE not found in .env file")

    if not GOOGLE_SHEETS_SPREADSHEET_ID:
        errors.append("GOOGLE_SHEETS_SPREADSHEET_ID not found in .env file")

    if errors:
        print("[ERROR] Configuration validation failed:")
        for error in errors:
            print(f"  - {error}")
        print("[INFO] Please check your .env file and try again.")
        return False
    return True

def get_yes_no_input(prompt: str) -> bool:
    while True:
        response = input(prompt).strip().lower()
        if response in ('y', 'yes'):
            return True
        elif response in ('n', 'no'):
            return False
        else:
            print("[ERROR] Invalid input. Please enter 'y' or 'n'.")

def get_menu_choice() -> str:
    while True:
        try:
            choice = input("\nSelect an operation (1-5): ").strip()
            if choice in ('1', '2', '3', '4', '5'):
                return choice
            else:
                print("[ERROR] Invalid selection. Please enter a number between 1 and 5.")
        except KeyboardInterrupt:
            print("\n[INFO] Operation cancelled by user.")
            return '5'
        except Exception as e:
            print(f"[ERROR] Unexpected input error: {e}")
            print("[INFO] Please try again.")

def get_last_sync_timestamp() -> float:
    if not os.path.exists(STATE_FILE):
        return None
    
    try:
        with open(STATE_FILE, "r") as f:
            content = f.read().strip()
            if not content:
                return None
            return float(content)
    except ValueError:
        print(f"[ERROR] State file contains invalid timestamp: {content}")
        return None
    except IOError as e:
        print(f"[ERROR] Failed to read state file: {e}")
        return None

def update_sync_timestamp(new_timestamp: float):
    if not isinstance(new_timestamp, (int, float)):
        print(f"[ERROR] Invalid timestamp value: {new_timestamp}")
        return False
    
    try:
        os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
        with open(STATE_FILE, "w") as f:
            f.write(str(new_timestamp))
        return True
    except IOError as e:
        print(f"[ERROR] Failed to write state file: {e}")
        return False

def extract_sql(db_path: str, last_sync: float = None) -> pd.DataFrame:
    if not db_path:
        print("[ERROR] Database path not provided. Check MM_DB_PATH in .env file.")
        return pd.DataFrame()
    
    if not isinstance(db_path, str):
        print(f"[ERROR] Database path must be a string, got {type(db_path).__name__}")
        return pd.DataFrame()
    
    if not os.path.exists(db_path):
        print(f"[ERROR] Database file not found at: {db_path}")
        print("[INFO] Please verify the MM_DB_PATH in your .env file.")
        return pd.DataFrame()
    
    if not os.path.isfile(db_path):
        print(f"[ERROR] Path exists but is not a file: {db_path}")
        return pd.DataFrame()

    uri = f"file:{os.path.abspath(db_path)}?mode=ro"
    
    query = """
        SELECT 
            t.ZDATE as timestamp,
            t.ZDO_TYPE as type,
            t.ZAMOUNT as amount,
            a.ZNICNAME as account_name,
            target.ZNICNAME as to_account_name,
            c.ZNAME as category_name,
            t.ZCONTENT as note
        FROM ZINOUTCOME t
        LEFT JOIN ZASSET a ON t.ZASSETUID = a.ZUID
        LEFT JOIN ZASSET target ON t.ZTOASSETUID = target.ZUID
        LEFT JOIN ZCATEGORY c ON t.ZCATEGORYUID = c.ZUID
        WHERE (t.ZISDEL = 0 OR t.ZISDEL IS NULL)
        AND t.ZDO_TYPE IN ('0', '1', '3', '4')
    """
    
    if last_sync:
        if not isinstance(last_sync, (int, float)):
            print(f"[ERROR] Invalid sync timestamp type: {type(last_sync).__name__}")
            return pd.DataFrame()
        query += f" AND t.ZDATE > {last_sync}"
        print(f"[INFO] Querying new records after timestamp {last_sync}...")
        
    try:
        with sqlite3.connect(uri, uri=True) as conn:
            return pd.read_sql_query(query, conn)
    except sqlite3.Error as e:
        print(f"[ERROR] Database connection failed: {e}")
        return pd.DataFrame()

def transform_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    
    df['date'] = pd.to_datetime(df['timestamp'] + 978307200, unit='s', errors='coerce')
    df['date'] = df['date'].dt.tz_localize('UTC').dt.tz_convert('Asia/Manila')
    
    df['note'] = df['note'].fillna('Untitled Transaction').astype(str).str.strip().replace(['', 'None', 'nan'], 'Untitled Transaction')
    
    is_transfer = df['type'].astype(str).isin(['3', '4'])
    df.loc[is_transfer, 'category_name'] = 'Transfer'
    
    def enrich_transfer_note(row):
        other_acc = str(row['to_account_name']).strip()
        if other_acc in ['', 'None', 'nan']:
            other_acc = "Unknown Account"
            
        if str(row['type']) == '3':
            return f"To: {other_acc} | {row['note']}"
        if str(row['type']) == '4':
            return f"From: {other_acc} | {row['note']}"
        return row['note']
    
    df.loc[is_transfer, 'note'] = df[is_transfer].apply(enrich_transfer_note, axis=1)
    
    df['category_name'] = df['category_name'].fillna('Uncategorized').astype(str).str.strip().replace(['', 'None', 'nan'], 'Uncategorized')
    df['account_name'] = df['account_name'].fillna('Unknown Account').astype(str).str.strip().replace(['', 'None', 'nan'], 'Unknown Account')
    
    type_map = {'0': 'Income', '1': 'Expense', '3': 'Transfer', '4': 'Transfer'}
    df['type'] = df['type'].astype(str).map(type_map).fillna('Other')
    
    df['amount'] = df['amount'].abs() 
    
    if 'to_account_name' in df.columns:
        df = df.drop(columns=['to_account_name'])
        
    return df

def export_to_csv(df: pd.DataFrame, filename: str = os.path.join("output", "Money_Manager_Export.csv")):
    df_csv = df.drop(columns=['timestamp'])

    df_csv['date'] = df_csv['date'].dt.strftime('%Y-%m-%d %H:%M')

    df_csv = df_csv.rename(columns={
        'date': 'DATE',
        'type': 'TYPE',
        'account_name': 'ACCOUNT',
        'category_name': 'CATEGORY',
        'amount': 'AMOUNT',
        'note': 'DETAILS / NAME'
    })

    column_order = ['DATE', 'TYPE', 'ACCOUNT', 'CATEGORY', 'AMOUNT', 'DETAILS / NAME']
    df_csv = df_csv[column_order]

    try:
        os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
        df_csv.to_csv(filename, index=False)
        print(f"[SUCCESS] Exported {len(df_csv)} records to {filename}")
        print("[INFO] Next Step: Import this file into your Google Sheets budget tracker.")
        return True
    except Exception as e:
        print(f"[ERROR] CSV export failed: {e}")
        return False

def export_setup_data(df: pd.DataFrame):
    types = sorted([str(x) for x in df['type'].unique() if pd.notna(x)])
    accounts = sorted([str(x) for x in df['account_name'].unique() if pd.notna(x)])
    categories = sorted([str(x) for x in df['category_name'].unique() if pd.notna(x)])
    
    max_len = max(len(types), len(accounts), len(categories))
    
    setup_df = pd.DataFrame({
        'UNIQUE TYPES': types + [''] * (max_len - len(types)),
        'UNIQUE ACCOUNTS': accounts + [''] * (max_len - len(accounts)),
        'UNIQUE CATEGORIES': categories + [''] * (max_len - len(categories))
    })
    
    filename = os.path.join("output", "Sheet_Setup_Data.csv")
    try:
        os.makedirs(os.path.dirname(filename), exist_ok=True)
        setup_df.to_csv(filename, index=False)
        print(f"[SUCCESS] Exported unique lists to {filename}")
        return True
    except Exception as e:
        print(f"[ERROR] Setup data export failed: {e}")
        return False

def retry_with_backoff(fn, max_retries: int = 3, base_delay: int = 2):
    for attempt in range(max_retries):
        try:
            return fn()
        except Exception:
            if attempt == max_retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))

def get_sheets_client():
    try:
        return gspread.service_account(filename=GOOGLE_SERVICE_ACCOUNT_FILE)
    except Exception as e:
        print(f"[ERROR] Google Sheets authentication failed: {e}")
        print("[INFO] Check GOOGLE_SERVICE_ACCOUNT_FILE in your .env file.")
        return None

def get_worksheet(client, worksheet_name: str = "Transactions Log"):
    try:
        return retry_with_backoff(lambda: client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID).worksheet(worksheet_name))
    except Exception as e:
        print(f"[ERROR] Failed to open worksheet '{worksheet_name}': {e}")
        print("[INFO] Check GOOGLE_SHEETS_SPREADSHEET_ID and that the sheet is shared with the service account.")
        return None

def get_setup_categories(client) -> set:
    try:
        rows = retry_with_backoff(lambda: client.open_by_key(GOOGLE_SHEETS_SPREADSHEET_ID).worksheet("Setup").get_all_values())
    except Exception as e:
        print(f"[WARNING] Could not read Setup sheet to check for new categories: {e}")
        return set()

    categories = set()
    for row in rows[1:]:
        for value in row:
            value = value.strip()
            if value:
                categories.add(value)
    return categories

def log_new_categories(categories: list, filename: str = os.path.join("logs", "unbucketed_categories.log")) -> bool:
    if not categories:
        return True

    try:
        already_logged = set()
        if os.path.exists(filename):
            with open(filename, "r") as f:
                already_logged = {line.strip() for line in f if line.strip()}

        to_append = [c for c in categories if c not in already_logged]
        if to_append:
            os.makedirs(os.path.dirname(filename) or ".", exist_ok=True)
            with open(filename, "a") as f:
                for category in to_append:
                    f.write(category + "\n")
        return True
    except IOError as e:
        print(f"[ERROR] Failed to persist new category warnings to {filename}: {e}")
        return False

def flag_new_categories(df: pd.DataFrame, known_categories: set):
    seen = set(df['category_name'].unique()) - SYNTHETIC_CATEGORIES
    new_categories = sorted(seen - known_categories)
    for category in new_categories:
        print(f"[WARNING] New category \"{category}\" not found in Setup sheet - add it to the correct bucket (Income/Fixed Expenses/Expense/Savings/Debts).")
    if new_categories:
        log_new_categories(new_categories)

def filter_duplicate_rows(rows: list, existing_rows: list) -> list:
    existing_keys = set()
    for existing in existing_rows:
        if len(existing) != 6:
            continue
        date, type_, account, category, amount, note = existing
        try:
            amount = round(float(amount), 2)
        except ValueError:
            continue
        existing_keys.add((date, type_, account, category, amount, note))

    return [
        row for row in rows
        if (row[0], row[1], row[2], row[3], round(float(row[4]), 2), row[5]) not in existing_keys
    ]

def push_to_sheet(worksheet, df: pd.DataFrame) -> bool:
    ordered = df.sort_values('date', ascending=False)

    rows = ordered.apply(lambda row: [
        row['date'].strftime('%Y-%m-%d %H:%M'),
        row['type'],
        row['account_name'],
        row['category_name'],
        row['amount'],
        row['note'],
    ], axis=1).tolist()

    try:
        existing_rows = retry_with_backoff(lambda: worksheet.get_all_values())[2:]
    except Exception as e:
        print(f"[ERROR] Failed to read existing rows for duplicate check: {e}")
        return False

    new_rows = filter_duplicate_rows(rows, existing_rows)
    skipped = len(rows) - len(new_rows)
    if skipped:
        print(f"[INFO] Skipped {skipped} row(s) already present in '{worksheet.title}'.")

    if not new_rows:
        print("[INFO] Nothing new to push after duplicate check.")
        return True

    try:
        retry_with_backoff(lambda: worksheet.insert_rows(new_rows, row=3, value_input_option='USER_ENTERED'))
        print(f"[SUCCESS] Pushed {len(new_rows)} records to '{worksheet.title}'.")
        return True
    except Exception as e:
        print(f"[ERROR] Failed to push records to Google Sheets: {e}")
        return False

def main():
    if not validate_environment():
        return

    while True:
        print("\n" + "="*50)
        print(" Money Manager to Google Sheets ETL Pipeline")
        print("="*50)
        print("1. Full History Export (CSV)")
        print("   - Extracts full historical data to CSV for initial setup.")
        print("   - Establishes the sync state to track new records.")
        print("\n2. New Transactions Sync (Google Sheets)")
        print("   - Extracts new transactions since the last run and pushes them")
        print("     directly into the 'Transactions Log' tab of your Google Sheet.")
        print("\n3. Reset Sync State")
        print("   - Deletes the local state file. Resets the pipeline to zero.")
        print("\n4. Export Sheet Setup Data")
        print("   - Extracts unique Types, Accounts, and Categories for Sheet setup.")
        print("\n5. Exit")
        print("="*50)

        choice = get_menu_choice()

        if choice == '1':
            print("\n[INFO] Executing Full History Export...")
            raw_df = extract_sql(DB_PATH)
            if not raw_df.empty:
                clean_df = transform_data(raw_df)
                if export_to_csv(clean_df, os.path.join("output", "Money_Manager_Full_Export.csv")):
                    if update_sync_timestamp(raw_df['timestamp'].max()):
                        print("[INFO] Sync state established. Ready for future incremental runs.")
                    else:
                        print("[WARNING] CSV exported but sync state could not be saved.")
                else:
                    print("[ERROR] CSV export failed.")
            else:
                print("[ERROR] No data extracted from database.")

        elif choice == '2':
            print("\n[INFO] Executing New Transactions Sync...")
            last_sync = get_last_sync_timestamp()

            if last_sync is None:
                print("[WARNING] No sync state found. Run Option 1 first to establish a baseline.")
                if not get_yes_no_input("Proceed with full historical export? (y/n): "):
                    print("[INFO] Export cancelled.")
                    continue

            raw_df = extract_sql(DB_PATH, last_sync)
            if raw_df.empty:
                print("[INFO] No new transactions detected. Pipeline up to date.")
            else:
                clean_df = transform_data(raw_df)

                client = get_sheets_client()
                worksheet = get_worksheet(client) if client else None

                if worksheet is None:
                    print("[ERROR] Sync aborted. Could not connect to Google Sheets.")
                else:
                    flag_new_categories(clean_df, get_setup_categories(client))
                    if push_to_sheet(worksheet, clean_df):
                        if update_sync_timestamp(raw_df['timestamp'].max()):
                            print("[INFO] Sync state successfully updated.")
                        else:
                            print("[WARNING] Sheet updated but sync state could not be saved.")
                    else:
                        print("[ERROR] Failed to push transactions to Google Sheets.")

        elif choice == '3':
            print("\n[INFO] Resetting Sync State...")
            if not get_yes_no_input("Are you sure? This will reset the sync state. (y/n): "):
                print("[INFO] Reset cancelled.")
                continue

            try:
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                    print("[SUCCESS] Sync state cleared. Pipeline will treat database as new.")
                else:
                    print("[INFO] No active sync state file found.")
            except Exception as e:
                print(f"[ERROR] Failed to reset sync state: {e}")

        elif choice == '4':
            print("\n[INFO] Exporting Sheet Setup Data...")
            raw_df = extract_sql(DB_PATH)
            if not raw_df.empty:
                clean_df = transform_data(raw_df)
                export_setup_data(clean_df)
            else:
                print("[ERROR] No data extracted from database.")

        elif choice == '5':
            print("[INFO] Exiting pipeline.")
            break

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Program interrupted by user.")
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        print("[INFO] Please check the logs above for details.")