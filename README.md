# Money Manager to Google Sheets ETL

An automated Python ETL (Extract, Transform, Load) tool that extracts your mobile financial data from the **Money Manager** app and formats it for easy import into a **Google Sheets** budget tracker.

## Features
* **Bypasses SQLite Locks:** Extracts data safely even if the app is syncing by using read-only URI modes.
* **Smart Formatting:** Automatically reorders and renames columns to match standard budget tracker formats: `DATE`, `TYPE`, `ACCOUNT`, `CATEGORY`, `AMOUNT`, `DETAILS / NAME`.
* **Type Mapping:** Converts internal database flags into human-readable "Income" and "Expense" labels.
* **Incremental Export:** Utilizes a local state file (`.sync_state`) to track the high-water mark of exported transactions, allowing you to export only new data since your last run.
* **Sheet Setup Assistant:** Generates a separate CSV with unique Categories, Accounts, and Types to help you set up your Google Sheet quickly.

## Prerequisites
* Python 3.9+
* A Money Manager (Realbyte) backup file (`.mmbak` or `.sqlite`).

---

## 1. Installation

1. **Clone the repository:**
   ```bash
   git clone https://github.com/rdgonzaga/money-manager-to-notion
   cd money-manager-to-notion
   pip install -r requirements.txt
   ```

2. **Create a `.env` file** in the project root:
   ```bash
   cp .env.example .env
   ```

3. **Edit `.env`** with the path to your database:
   ```
   MM_DB_PATH=/path/to/Money/Manager/database.mmbak
   ```

---

## 2. Configuration

### Get Your Money Manager Database Path
The Money Manager app stores transaction data in a SQLite database.
- Export the `.mmbak` file from the app's backup settings.
- Move it to a location accessible by the script.
- Update `MM_DB_PATH` in your `.env` file with the absolute path.

---

## 3. Usage

Run the script:
```bash
python money_manager_to_notion.py
```

You'll see an interactive menu with the following options:

### Option 1: Full History Export (CSV)
- **When to use:** First time setting up your Google Sheet.
- **What it does:** Extracts every transaction in your database to `Money_Manager_Full_Export.csv`.
- **Result:** Establishes a "sync state" so future exports only include new data.

### Option 2: New Transactions Export (CSV)
- **When to use:** Weekly or monthly updates.
- **What it does:** Extracts only the transactions added since your last export to `Money_Manager_New_Transactions.csv`.
- **Result:** Keeps your Google Sheet up-to-date without duplicates.

### Option 3: Reset Sync State
- **When to use:** If you want to re-export your entire history or started a new Sheet.
- **What it does:** Deletes the local `.sync_state` file.

### Option 4: Export Sheet Setup Data
- **When to use:** When configuring your Google Sheet categories and accounts.
- **What it does:** Generates `Sheet_Setup_Data.csv` containing unique lists of your Categories, Accounts, and Types.
- **Result:** Allows you to copy-paste your exact app configuration into your Sheet setup.

### Option 5: Exit
- Safely closes the program.

---

## 4. CSV Structure

The exported files use the following column headers and order:

| Column | Description |
| :--- | :--- |
| **DATE** | The date and time of the transaction (YYYY-MM-DD HH:MM). |
| **TYPE** | "Income" or "Expense". |
| **ACCOUNT** | The source account (e.g., Cash, Bank, Credit Card). |
| **CATEGORY** | The category (e.g., Food, Transport, Salary). |
| **AMOUNT** | The monetary value (always positive). |
| **DETAILS / NAME** | The note or description of the transaction. |

---

## 5. Troubleshooting

**Q: The script says "Database connection failed"**
- Verify the `MM_DB_PATH` in your `.env` file.
- Use the full absolute path (e.g., `C:\Users\Name\Documents\backfile.mmbak`).

**Q: How do I import the CSV into Google Sheets?**
1. In Google Sheets, go to **File > Import**.
2. Upload the generated CSV file.
3. Select **"Append to current sheet"** or **"Insert new sheet(s)"**.

---

## License
See [LICENSE](LICENSE) file for details.