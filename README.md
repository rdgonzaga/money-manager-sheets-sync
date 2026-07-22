# Money Manager to Google Sheets ETL

An automated Python ETL (Extract, Transform, Load) tool that extracts your mobile financial data from the **Money Manager** app and syncs it into a **Google Sheets** budget tracker.

## Features
* **Bypasses SQLite Locks:** Extracts data safely even if the app is syncing by using read-only URI modes.
* **Direct Google Sheets Sync:** Pushes new transactions straight into your sheet's "Transactions Log" tab via the Google Sheets API — no manual copy/paste.
* **Smart Formatting:** Automatically reorders and renames columns to match standard budget tracker formats: `DATE`, `TYPE`, `ACCOUNT`, `CATEGORY`, `AMOUNT`, `DETAILS / NAME`.
* **Type Mapping:** Converts internal database flags into human-readable "Income", "Expense", and "Transfer" labels.
* **Incremental Sync:** Utilizes a local state file (`data/.sync_state`) to track the high-water mark of synced transactions, so each run only pushes new data since the last one.
* **New Category Guardrails:** Warns you when a transaction uses a category that isn't in your sheet's `Setup` tab yet, so nothing silently falls out of your budget totals.
* **Sheet Setup Assistant:** Generates a CSV with unique Categories, Accounts, and Types to help you set up your Google Sheet quickly.

## Prerequisites
* Python 3.9+
* A Money Manager (Realbyte) backup file (`.mmbak` or `.sqlite`).
* A Google Sheet with a "Transactions Log" tab whose header row matches `DATE, TYPE, ACCOUNT, CATEGORY, AMOUNT, DETAILS / NAME`, and (optionally) a "Setup" tab listing your budget categories.
* A Google Cloud service account with access to that sheet (see setup below).

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

---

## 2. Configuration

### Money Manager Database Path
The Money Manager app stores transaction data in a SQLite database.
- Export the `.mmbak` file from the app's backup settings.
- Move it into the `data/` folder (created automatically the first time you run the script, or create it yourself).
- Set `MM_DB_PATH` in your `.env` file to that file's path, e.g. `./data/backfile.mmbak`.

### Google Sheets API Setup
The sync pushes rows directly into your sheet using a **service account** — a dedicated Google identity for the script, so you never have to make the sheet public.

1. In the [Google Cloud Console](https://console.cloud.google.com/), create (or select) a project and enable the **Google Sheets API**.
2. Under IAM & Admin > Service Accounts, create a new service account, then generate and download a **JSON key** for it. Save it into the project's `credentials/` folder (e.g. `credentials/google_service_account.json`) — that folder is gitignored by default.
3. Open the JSON key and copy the `client_email` value.
4. In your Google Sheet, click **Share** and add that email address with **Editor** access. The sheet itself can stay fully private otherwise.
5. Copy the spreadsheet ID from the sheet's URL: `https://docs.google.com/spreadsheets/d/`**`THIS_PART`**`/edit`.
6. Set both in your `.env` file:
   ```
   GOOGLE_SERVICE_ACCOUNT_FILE=./credentials/google_service_account.json
   GOOGLE_SHEETS_SPREADSHEET_ID=your_spreadsheet_id_here
   ```

---

## 3. Usage

Run the script:
```bash
python money_manager_to_notion.py
```

You'll see an interactive menu with 5 options. After each operation finishes, the menu redisplays so you can run another one without restarting the script.

### Option 1: Full History Export (CSV)
- **When to use:** Bootstrapping a brand-new Google Sheet, or rebuilding one from scratch.
- **What it does:** Extracts every transaction in your database to `output/Money_Manager_Full_Export.csv` for manual import (File > Import in Google Sheets), and establishes the sync state.
- **Note:** This is CSV-only on purpose — running it against a sheet that already has transactions in it (from a previous sync) would create duplicates. Use Option 2 for your regular ongoing syncs.

### Option 2: New Transactions Sync (Google Sheets)
- **When to use:** Your regular sync — weekly, monthly, whenever you export a fresh `.mmbak`.
- **What it does:** Extracts only the transactions added since your last sync, warns you about any category not yet listed in your sheet's `Setup` tab, and inserts the new rows directly into the top of "Transactions Log" (preserving newest-first order) via the Google Sheets API.
- **Result:** Your sheet stays up to date with no manual copy/paste, and no duplicates.

### Option 3: Reset Sync State
- **When to use:** If you want to re-sync your entire history or started a new Sheet.
- **What it does:** Deletes the local `data/.sync_state` file.

### Option 4: Export Sheet Setup Data
- **When to use:** When configuring your Google Sheet categories and accounts.
- **What it does:** Generates `output/Sheet_Setup_Data.csv` containing unique lists of your Categories, Accounts, and Types.
- **Result:** Allows you to copy-paste your exact app configuration into your Sheet setup.

### Option 5: Exit
- Safely closes the program.

---

## 4. CSV Structure

Files produced by Options 1 and 4 (and the columns pushed directly to Google Sheets by Option 2) use the following column headers and order:

| Column | Description |
| :--- | :--- |
| **DATE** | The date and time of the transaction (YYYY-MM-DD HH:MM). |
| **TYPE** | "Income", "Expense", or "Transfer". |
| **ACCOUNT** | The source account (e.g., Cash, Bank, Credit Card). |
| **CATEGORY** | The category (e.g., Food, Transport, Salary). |
| **AMOUNT** | The monetary value (always positive). |
| **DETAILS / NAME** | The note or description of the transaction. |

---

## 5. Project Layout

All runtime files are gitignored and created automatically as needed:

| Folder | Contents |
| :--- | :--- |
| `data/` | Your `.mmbak`/`.sqlite` backup and the `.sync_state` watermark file. |
| `output/` | Generated CSVs (Options 1, 2, 4). |
| `credentials/` | Your Google service account JSON key. |
| `logs/` | `unbucketed_categories.log`. |

---

## 6. Troubleshooting

**Q: The script says "Database connection failed"**
- Verify the `MM_DB_PATH` in your `.env` file.
- Use the full absolute path (e.g., `C:\Users\Name\Documents\backfile.mmbak`).

**Q: The script says "Google Sheets authentication failed" or a `PermissionError` when opening the worksheet**
- Confirm `GOOGLE_SERVICE_ACCOUNT_FILE` points to a valid JSON key and `GOOGLE_SHEETS_SPREADSHEET_ID` matches your sheet's URL.
- Confirm the sheet is shared with your service account's `client_email` as **Editor** (open the JSON key to find it).
- Confirm the **Google Sheets API** is enabled for that Google Cloud project.

**Q: I'm seeing `[WARNING] New category "X" not found in Setup sheet`**
- This means a transaction used a category not currently listed in your sheet's `Setup` tab. The sync still succeeds — it's just a heads-up. Add the category to the correct bucket in `Setup` (Income/Fixed Expenses/Expense/Savings/Debts) so it gets counted in your Budget totals. It's also logged to `logs/unbucketed_categories.log`.

**Q: How do I import a CSV into Google Sheets (Options 1 or 4)?**
1. In Google Sheets, go to **File > Import**.
2. Upload the generated CSV file.
3. Select **"Append to current sheet"** or **"Insert new sheet(s)"**.

---

## License
See [LICENSE](LICENSE) file for details.
