import os
import sqlite3
import pandas as pd
from dotenv import load_dotenv
from notion_client import Client
from notion_client.errors import APIResponseError
import time

# ----------------- CONFIGURATION -----------------
load_dotenv()
NOTION_TOKEN = os.getenv("NOTION_API_KEY")
FINANCE_DB_ID = os.getenv("NOTION_FINANCE_DB_ID")
DB_PATH = os.getenv("MM_DB_PATH")
STATE_FILE = ".sync_state" 

notion = Client(auth=NOTION_TOKEN) if NOTION_TOKEN else None

# --------- VALIDATION & ERROR CHECKING ---------
def validate_environment():
    """Validate required environment variables are set."""
    errors = []
    
    if not NOTION_TOKEN:
        errors.append("NOTION_API_KEY not found in .env file")
    if not FINANCE_DB_ID:
        errors.append("NOTION_FINANCE_DB_ID not found in .env file")
    if not DB_PATH:
        errors.append("MM_DB_PATH not found in .env file")
    
    if errors:
        print("[ERROR] Configuration validation failed:")
        for error in errors:
            print(f"  - {error}")
        print("[INFO] Please check your .env file and try again.")
        return False
    return True

def get_yes_no_input(prompt: str) -> bool:
    """Get validated yes/no input from user."""
    while True:
        response = input(prompt).strip().lower()
        if response in ('y', 'yes'):
            return True
        elif response in ('n', 'no'):
            return False
        else:
            print("[ERROR] Invalid input. Please enter 'y' or 'n'.")

def get_menu_choice() -> str:
    """Get validated menu choice from user."""
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

# ----------------- STATE MANAGEMENT -----------------
def get_last_sync_timestamp() -> float:
    """Retrieve the last sync timestamp from state file."""
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
    """Save the sync timestamp to state file."""
    if not isinstance(new_timestamp, (int, float)):
        print(f"[ERROR] Invalid timestamp value: {new_timestamp}")
        return False
    
    try:
        with open(STATE_FILE, "w") as f:
            f.write(str(new_timestamp))
        return True
    except IOError as e:
        print(f"[ERROR] Failed to write state file: {e}")
        return False

# ----------------- EXTRACT -----------------
def extract_sql(db_path: str, last_sync: float = None) -> pd.DataFrame:
    """Extract transaction data from Money Manager database."""
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
            c.ZNAME as category_name,
            t.ZCONTENT as note
        FROM ZINOUTCOME t
        LEFT JOIN ZASSET a ON t.ZASSETUID = a.ZUID
        LEFT JOIN ZCATEGORY c ON t.ZCATEGORYUID = c.ZUID
        WHERE (t.ZISDEL = 0 OR t.ZISDEL IS NULL)
        AND t.ZDO_TYPE IN ('0', '1')
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

# ----------------- TRANSFORM -----------------
def transform_data(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty: return df
    
    df['date'] = pd.to_datetime(df['timestamp'] + 978307200, unit='s', errors='coerce')
    df['date'] = df['date'].dt.tz_localize('UTC').dt.tz_convert('Asia/Manila')
    df['note'] = df['note'].astype(str).str.strip().replace(['', 'None', 'nan'], 'Untitled Transaction')
    df['category_name'] = df['category_name'].astype(str).str.strip().replace(['', 'None', 'nan'], 'Uncategorized')
    df['account_name'] = df['account_name'].astype(str).str.strip().replace(['', 'None', 'nan'], 'Unknown Account')
    
    # Map transaction type (0 = Income, 1 = Expense)
    type_map = {'0': 'Income', '1': 'Expense'}
    df['type'] = df['type'].astype(str).map(type_map).fillna('Other')
    
    df['amount'] = df['amount'].abs() 
    return df

# ----------------- LOAD (API) -----------------
def load_to_notion(df: pd.DataFrame, database_id: str):
    """Upload transformed data to Notion database."""
    if not isinstance(df, pd.DataFrame):
        print(f"[ERROR] Expected DataFrame, got {type(df).__name__}")
        return False
    
    if not database_id:
        print("[ERROR] Database ID not provided. Check NOTION_FINANCE_DB_ID in .env file.")
        return False
    
    if not isinstance(database_id, str):
        print(f"[ERROR] Database ID must be a string, got {type(database_id).__name__}")
        return False
    
    if not notion:
        print("[ERROR] Notion API token is missing or invalid.")
        return False

    if df.empty:
        print("[WARNING] No records to sync.")
        return True
    
    print(f"[INFO] Initiating API sync for {len(df)} records...")
    for index, row in df.iterrows():
        payload = {
            "parent": {"database_id": database_id},
            "properties": {
                "Transaction": {"title": [{"text": {"content": str(row['note'])}}]},
                "Amount": {"number": float(row['amount'])},
                "Category": {"select": {"name": str(row['category_name'])}},
                "Account": {"select": {"name": str(row['account_name'])}},
                "Date": {"date": {"start": row['date'].isoformat()}}
            }
        }
        try:
            notion.pages.create(**payload)
            print(f"[SUCCESS] Synced: {row['date']} | {row['amount']} | {row['account_name']}")
        except APIResponseError as e:
            if e.code == "rate_limited":
                print("[WARNING] Rate limit encountered. Pausing execution for 5 seconds...")
                time.sleep(5)
                try:
                    notion.pages.create(**payload)
                except APIResponseError as retry_error:
                    print(f"[ERROR] Retry failed on row {index}: {retry_error}")
            else:
                print(f"[ERROR] API failure on row {index}: {e}")
        except Exception as e:
            print(f"[ERROR] Unexpected error on row {index}: {e}")
    
    return True

# ----------------- EXPORT (CSV) -----------------
def export_to_csv(df: pd.DataFrame):
    df_csv = df.drop(columns=['timestamp'])
    
    df_csv['date'] = df_csv['date'].dt.strftime('%Y-%m-%d %H:%M')
    
    # Rename columns to match Google Sheets format
    df_csv = df_csv.rename(columns={
        'date': 'DATE',
        'type': 'TYPE',
        'account_name': 'ACCOUNT',
        'category_name': 'CATEGORY',
        'amount': 'AMOUNT',
        'note': 'DETAILS / NAME'
    })
    
    # Reorder columns
    column_order = ['DATE', 'TYPE', 'ACCOUNT', 'CATEGORY', 'AMOUNT', 'DETAILS / NAME']
    df_csv = df_csv[column_order]
    
    filename = "Notion_Initial_Load.csv"
    
    try:
        df_csv.to_csv(filename, index=False)
        print(f"[SUCCESS] Exported {len(df_csv)} records to {filename}")
        print("[INFO] Next Step: Upload via Notion's 'Merge with CSV' feature or import to Google Sheets.")
        return True
    except Exception as e:
        print(f"[ERROR] CSV export failed: {e}")
        return False

def export_setup_data(df: pd.DataFrame):
    """Export unique Types, Accounts, and Categories for Sheet setup."""
    types = sorted(df['type'].unique())
    accounts = sorted(df['account_name'].unique())
    categories = sorted(df['category_name'].unique())
    
    # Pad lists to the same length to create a DataFrame
    max_len = max(len(types), len(accounts), len(categories))
    
    setup_df = pd.DataFrame({
        'UNIQUE TYPES': types + [''] * (max_len - len(types)),
        'UNIQUE ACCOUNTS': accounts + [''] * (max_len - len(accounts)),
        'UNIQUE CATEGORIES': categories + [''] * (max_len - len(categories))
    })
    
    filename = "Sheet_Setup_Data.csv"
    try:
        setup_df.to_csv(filename, index=False)
        print(f"[SUCCESS] Exported unique lists to {filename}")
        return True
    except Exception as e:
        print(f"[ERROR] Setup data export failed: {e}")
        return False

# ----------------- CLI MENU -----------------
def main():
    # Validate environment before starting
    if not validate_environment():
        return
    
    print("\n" + "="*50)
    print(" Money Manager to Notion ETL Pipeline")
    print("="*50)
    print("1. Initial Setup (CSV Export)")
    print("   - Extracts full historical data to CSV for bulk Notion upload.")
    print("   - Establishes the initial high-water mark for future syncs.")
    print("\n2. Incremental Sync (Notion API)")
    print("   - Queries and pushes only new transactions since the last sync.")
    print("\n3. Reset Sync State")
    print("   - Deletes the local state file. Resets the pipeline to zero.")
    print("\n4. Export Sheet Setup Data")
    print("   - Extracts unique Types, Accounts, and Categories for Sheet setup.")
    print("\n5. Exit")
    print("="*50)

    choice = get_menu_choice()

    if choice == '1':
        print("\n[INFO] Executing Initial CSV Setup...")
        raw_df = extract_sql(DB_PATH)
        if not raw_df.empty:
            clean_df = transform_data(raw_df)
            if export_to_csv(clean_df):
                if update_sync_timestamp(raw_df['timestamp'].max()):
                    print("[INFO] Sync state established. Ready for future incremental runs.")
                else:
                    print("[WARNING] CSV exported but sync state could not be saved.")
            else:
                print("[ERROR] CSV export failed.")
        else:
            print("[ERROR] No data extracted from database.")

    elif choice == '2':
        print("\n[INFO] Executing Incremental API Sync...")
        last_sync = get_last_sync_timestamp()
        
        if last_sync is None:
            print("[WARNING] No sync state found. Run Option 1 first to establish a baseline.")
            if not get_yes_no_input("Proceed with full historical API sync? This is not recommended. (y/n): "):
                print("[INFO] Sync cancelled.")
                return

        raw_df = extract_sql(DB_PATH, last_sync)
        if raw_df.empty:
            print("[INFO] No new transactions detected. Pipeline up to date.")
        else:
            clean_df = transform_data(raw_df)
            if load_to_notion(clean_df, FINANCE_DB_ID):
                if update_sync_timestamp(raw_df['timestamp'].max()):
                    print("[INFO] Sync state successfully updated.")
                else:
                    print("[WARNING] Transactions synced but sync state could not be saved.")
            else:
                print("[ERROR] Failed to sync transactions to Notion.")

    elif choice == '3':
        print("\n[INFO] Resetting Sync State...")
        if not get_yes_no_input("Are you sure? This will reset the sync state. (y/n): "):
            print("[INFO] Reset cancelled.")
            return
        
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

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[INFO] Program interrupted by user.")
    except Exception as e:
        print(f"\n[ERROR] Unexpected error: {e}")
        print("[INFO] Please check the logs above for details.")