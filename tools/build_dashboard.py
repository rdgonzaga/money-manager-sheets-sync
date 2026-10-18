#!/usr/bin/env python3
"""
Builds the "Dashboard" tab of the budget Google Sheet and applies one colour palette to the other tabs.

  python tools/build_dashboard.py            -> writes output/dashboard_requests.json + tabs_requests.json (dry run)
  python tools/build_dashboard.py --apply    -> builds/rebuilds everything with the project's service account
  add --show-helpers to keep the helper columns (AG:AZ) visible while debugging

Safe to re-run: the Dashboard tab is deleted and recreated, and the month, year and savings-goal
inputs are carried over. It only READS 'Transactions Log' and 'Budget' (plus Budget!D14:D15 now point
at the Dashboard pickers), so money_manager_to_notion.py keeps working untouched.

Rule: every Log range starts at row 2 (the header). The sync inserts rows at row 3, which only expands
a range that starts above row 3. A range starting at row 3 would silently skip every new transaction.
"""
import json, os, sys

SS_ID = "1GSWQDSliwPB_Zt5Dum4hrI72Uw6ECWTDYyuJIzLHmiQ"
DASH, BUDGET, LOG, SETUP, WISH = 700100200, 1327895224, 474710874, 1864868637, 349543603
L = "'Transactions Log'!"
# ranges start at the header row (row 2) on purpose: the sync inserts new rows at row 3, which only
# EXPANDS a range that starts above it. A range starting at row 3 would silently skip every new row.
A_, B_, C_, D_, E_, F_ = (f"{L}${c}$2:${c}" for c in "ABCDEF")
ALL_ = f"{L}$A$2:$F"
PAD6 = '{"","","","","",""}'                                   # one blank row (6 columns)
PAD = '{"","","","","","";"","","","","","";"","","","","","";"","","","","","";"","","","","",""}'   # 5 blank rows, so ARRAY_CONSTRAIN never errors on short months

# ------------------------------------------------------------------ palette
def rgb(h):
    h = h.lstrip("#")
    return {"red": round(int(h[0:2], 16) / 255, 3), "green": round(int(h[2:4], 16) / 255, 3), "blue": round(int(h[4:6], 16) / 255, 3)}

C = {k: rgb(v) for k, v in dict(
    bg="#ECE9F7", card="#FFFFFF", card2="#F2EFFB", line="#DCD6EE",
    text="#1B1633", muted="#473F66", dim="#6B6489",
    green="#3E9C7A", orange="#C78A3A", red="#C0494A", blue="#6D5BD0",
    purple="#A397DD", teal="#3E9C7A", pink="#6D5BD0", rt="#F6E0E0", wt="#F6EAD2", gt="#DCEFE5",
    # light tabs
    ink="#2E2557", page="#ECE9F7", zebra="#F7F5FC", acc="#6D5BD0", white="#FFFFFF", rule="#DCD6EE", body="#1B1633",
    g_h="#3E9C7A", g_t="#DCEFE5", b_h="#5B6BC0", b_t="#E3E6F6", p_h="#8B6FC9", p_t="#EBE3F7",
    t_h="#3E8FA0", t_t="#DAEDF1", r_h="#B85A5C", r_t="#F6E3E3", s_t="#E4E0F1",
).items()}

# ------------------------------------------------------------------ grid helpers
def col(n):
    s, n = "", n + 1
    while n:
        n, r = divmod(n - 1, 26)
        s = chr(65 + r) + s
    return s

def gs(g): return 1 + 5 * g          # first unit column of group g
def ge(g): return 4 + 5 * g          # last unit column of group g

def gr(r0, c0, r1, c1, sid=DASH):   # inclusive -> GridRange
    return {"sheetId": sid, "startRowIndex": r0, "endRowIndex": r1 + 1, "startColumnIndex": c0, "endColumnIndex": c1 + 1}

def a1(r, c, absolute=False):
    return f"${col(c)}${r + 1}" if absolute else f"{col(c)}{r + 1}"

fills, merges, cells, widths, heights, cfs, charts, extra = [], [], {}, [], [], [], [], []

def fill(r0, c0, r1, c1, color):
    fills.append({"repeatCell": {"range": gr(r0, c0, r1, c1), "cell": {"userEnteredFormat": {"backgroundColor": C[color]}},
                                 "fields": "userEnteredFormat.backgroundColor"}})

def merge(r0, c0, r1, c1):
    if (r0, c0) != (r1, c1):
        merges.append({"mergeCells": {"range": gr(r0, c0, r1, c1), "mergeType": "MERGE_ALL"}})

def put(r, c, v=None, bg="card", fg="text", size=10, bold=False, ha="LEFT", nf=None, italic=False, span=None, pad=False):
    """One cell (optionally merged across `span` = (r1, c1))."""
    fmt = {"backgroundColor": C[bg], "horizontalAlignment": ha, "verticalAlignment": "MIDDLE",
           "textFormat": {"foregroundColor": C[fg], "fontSize": size, "bold": bold, "italic": italic}}
    if nf:
        fmt["numberFormat"] = {"type": "CURRENCY" if "₱" in nf else ("PERCENT" if "%" in nf else "NUMBER"), "pattern": nf.replace("₱", "[$₱]") + ('"  "' if ha == "RIGHT" else "")}
    elif pad:
        fmt["numberFormat"] = {"type": "TEXT", "pattern": '"  "@'}
    cells[(r, c)] = (v, fmt)
    if span:
        merge(r, c, span[0], span[1])

def ev(v):
    if v is None: return {}
    if isinstance(v, str) and v.startswith("="): return {"formulaValue": v}
    if isinstance(v, (int, float)): return {"numberValue": v}
    return {"stringValue": v}

def emit_values():
    """Values/formulas only (no formats). Runs are split where there is a gap of 3+ empty cells."""
    out, rows = [], {}
    for (r, c), cv in cells.items():
        if cv[0] is not None: rows.setdefault(r, {})[c] = cv[0]
    for r in sorted(rows):
        cs = sorted(rows[r]); runs, cur = [], [cs[0]]
        for c in cs[1:]:
            if c - cur[-1] <= 40: cur.append(c)
            else: runs.append(cur); cur = [c]
        runs.append(cur)
        for run in runs:
            vals = [({"userEnteredValue": ev(rows[r][c])} if c in rows[r] else {}) for c in range(run[0], run[-1] + 1)]
            out.append({"updateCells": {"start": {"sheetId": DASH, "rowIndex": r, "columnIndex": run[0]}, "rows": [{"values": vals}], "fields": "userEnteredValue"}})
    return out

PAL_COL = 51   # palette cells live in the last column; they are cleared again at the end

def emit_formats():
    """Each distinct format is written once into a palette cell, then copied (format only) onto
    every rectangle that uses it. Keeps the request list ~3x smaller than repeating the format."""
    groups = {}
    for (r, c), (v, fmt) in cells.items():
        groups.setdefault(json.dumps(fmt, sort_keys=True), []).append((r, c))
    pal, copy = [], []
    for k, (key, pts) in enumerate(groups.items()):
        pal.append({"updateCells": {"start": {"sheetId": DASH, "rowIndex": k, "columnIndex": PAL_COL}, "rows": [{"values": [{"userEnteredFormat": json.loads(key)}]}], "fields": "userEnteredFormat"}})
        byrow = {}
        for r, c in pts: byrow.setdefault(r, []).append(c)
        runs = {}
        for r, cs in byrow.items():
            cs.sort(); a_ = b_ = cs[0]
            for c in cs[1:]:
                if c == b_ + 1: b_ = c
                else: runs.setdefault((a_, b_), []).append(r); a_ = b_ = c
            runs.setdefault((a_, b_), []).append(r)
        for (c0, c1), rs in runs.items():
            rs.sort(); a_ = b_ = rs[0]; spans = []
            for r in rs[1:]:
                if r == b_ + 1: b_ = r
                else: spans.append((a_, b_)); a_ = b_ = r
            spans.append((a_, b_))
            for (r0, r1) in spans:
                copy.append({"copyPaste": {"source": gr(k, PAL_COL, k, PAL_COL), "destination": gr(r0, c0, r1, c1), "pasteType": "PASTE_FORMAT"}})
    clean = [{"repeatCell": {"range": gr(0, PAL_COL, len(groups), PAL_COL), "cell": {"userEnteredFormat": {}}, "fields": "userEnteredFormat"}}]
    return pal, copy, clean

# ------------------------------------------------------------------ dimensions
H0, H1 = 32, 33            # helper label / value columns (AG / AH)
widths.append((0, 1, 16))
for g in range(6):
    for u in range(4): widths.append((gs(g) + u, gs(g) + u + 1, 44))
    if g < 5: widths.append((5 + 5 * g, 6 + 5 * g, 10))
widths += [(30, 31, 16), (31, 32, 30), (32, 33, 150), (33, 34, 120), (34, 52, 110)]

ROWS = {0: 14, 1: 40, 2: 22, 3: 14, 4: 28, 5: 22, 6: 56, 7: 22, 8: 10, 9: 20, 10: 20, 11: 12, 12: 14, 13: 28, 24: 10, 25: 14,
        26: 28, 27: 26, 38: 24, 39: 12, 40: 14, 41: 28, 47: 12, 48: 14, 49: 24, 50: 34, 51: 12}
for r in range(14, 24): ROWS[r] = 22
for r in range(28, 38): ROWS[r] = 28
for r in range(42, 47): ROWS[r] = 28
LAST_ROW = 52

# ------------------------------------------------------------------ helper block (hidden at the end)
HN = {}                                       # name -> absolute ref
helper_rows = [
    ("start", "=DATEVALUE(1&$V$3&$AA$3)"),
    ("end", "=EOMONTH({start},0)+1"),
    ("dim", "=DAY({end}-1)"),
    ("today", "=IF(TODAY()<{start},0,IF(TODAY()>={end},{dim},DAY(TODAY())))"),
    ("left", "={dim}-{today}"),
    ("income", "=Budget!E33"),
    ("spent", "=Budget!I27+Budget!M33"),
    ("plan", "=Budget!H27+Budget!L33"),
    ("saved", "=Budget!I32"),
    ("remain", "=Budget!E43"),
    ("incplan", "=Budget!D33"),
    ("safe", "=IF({left}>0,MAX(0,{plan}-{spent})/{left},0)"),
    ("used", "=IF({plan}>0,{spent}/{plan},0)"),
    ("expected", "=IF({dim}>0,{today}/{dim},0)"),
    ("srate", "=IF({income}>0,{saved}/{income},0)"),
    ("last", f"=MAX({A_})"),
    ("cnt", f'=COUNTIFS({A_},">="&{{start}},{A_},"<"&{{end}},{B_},"<>Transfer")'),
    ("untitled", f'=COUNTIFS({A_},">="&{{start}},{A_},"<"&{{end}},{B_},"<>Transfer",{F_},"Untitled Transaction")'),
    ("sbal", f'=SUMIFS({E_},{F_},"To: Personal Savings*")-SUMIFS({E_},{F_},"From: Personal Savings*")'),
    ("avg3", "=AVERAGE(AJ31:AJ33)"),
    ("recv", f'=SUMIFS({E_},{F_},"To: Receivables*")-SUMIFS({E_},{F_},"From: Receivables*")'),
    ("unplanned", "=IFERROR(SUMPRODUCT((AH70:AH91*1=0)*(AI70:AI91>0)),0)"),
    ("vsavg", "=IFERROR({spent}/AVERAGE(AI30:AI32)-1,\"\")"),
    ("unmapped", "=Budget!M32"),
]
for i, (n, _) in enumerate(helper_rows):
    HN[n] = f"$AH${3 + i}"
# trend table rows are 26-31, avg3 is the last three months of savings flow (AK29:AK31)
helper_rows = [(n, f) for n, f in helper_rows]
for i, (n, f) in enumerate(helper_rows):
    f = f.format(**HN) if "{" in f else f
    put(2 + i, H0, n, bg="bg", fg="dim", size=9)
    put(2 + i, H1, f, bg="bg", fg="muted", size=9)
START, END, DIM, TODAY_, LEFT, INC, SPENT, PLAN, SAVED, REMAIN, INCPLAN, SAFE, USED, EXPECTED, SRATE, LAST, CNT, UNT, SBAL, AVG3, RECV, UNPL, VSAVG, UNMAPPED = [HN[n] for n, _ in helper_rows]

put(1, H0, "DATA · do not edit (feeds the Dashboard)", bg="bg", fg="dim", size=9, bold=True)
# monthly trend (6 months ending at the selected month)
for j, h in enumerate(["Month", "Income", "Spent", "Saved flow"]):
    put(26, H0 + j, h, bg="bg", fg="muted", size=9, bold=True)
for i in range(6):
    r = 27 + i
    m = f"EDATE({START},{i - 5})"
    win = f'{A_},">="&{m},{A_},"<"&EDATE({m},1)'
    put(r, H0, f'=TEXT({m},"mmm")', bg="bg", fg="muted", size=9)
    put(r, H0 + 1, f'=SUMIFS({E_},{B_},"Income",{win})', bg="bg", fg="muted", size=9)
    put(r, H0 + 2, f'=SUMIFS({E_},{B_},"Expense",{win})', bg="bg", fg="muted", size=9)
    put(r, H0 + 3, f'=SUMIFS({E_},{F_},"To: Personal Savings*",{win})-SUMIFS({E_},{F_},"From: Personal Savings*",{win})', bg="bg", fg="muted", size=9)
# cumulative spend vs plan
for j, h in enumerate(["Day", "Actual", "Plan pace"]):
    put(34, H0 + j, h, bg="bg", fg="muted", size=9, bold=True)
for d in range(1, 32):
    r = 34 + d
    put(r, H0, d, bg="bg", fg="muted", size=9)
    put(r, H0 + 1, f'=IF(OR($AG{r + 1}>{DIM},$AG{r + 1}>{TODAY_}),NA(),SUMIFS({E_},{B_},"Expense",{A_},">="&{START},{A_},"<"&({START}+$AG{r + 1})))', bg="bg", fg="muted", size=9)
    put(r, H0 + 2, f'=IF($AG{r + 1}>{DIM},NA(),{PLAN}*$AG{r + 1}/{DIM})', bg="bg", fg="muted", size=9)
# sorted categories (fixed + variable from Budget), by actual desc
put(68, H0, "Category", bg="bg", fg="muted", size=9, bold=True); put(68, H0 + 1, "Planned", bg="bg", fg="muted", size=9, bold=True); put(68, H0 + 2, "Actual", bg="bg", fg="muted", size=9, bold=True)
put(69, H0, '=IFERROR(SORT(FILTER({Budget!G19:I26;Budget!K19:M32},(({Budget!H19:H26;Budget!L19:L32})>0)+(({Budget!I19:I26;Budget!M19:M32})>0)>0),3,FALSE),"")', bg="bg", fg="muted", size=9)
# donut data: top 5 + everything else
put(68, 36, "Category", bg="bg", fg="muted", size=9, bold=True); put(68, 37, "Spend", bg="bg", fg="muted", size=9, bold=True)
for i in range(5):
    put(69 + i, 36, f'=IFERROR(IF(INDEX($AG$70:$AG$91,{i + 1})="","—",INDEX($AG$70:$AG$91,{i + 1})),"—")', bg="bg", fg="muted", size=9)
    put(69 + i, 37, f'=IFERROR(INDEX($AI$70:$AI$91,{i + 1})*1,0)', bg="bg", fg="muted", size=9)
put(74, 36, "Everything else", bg="bg", fg="muted", size=9)
put(74, 37, f"=MAX({SPENT}-SUM(AL70:AL74),0)", bg="bg", fg="muted", size=9)
# biggest expenses / latest entries
put(68, 39, "Biggest expenses (month)", bg="bg", fg="muted", size=9, bold=True)
put(69, 39, f'=ARRAY_CONSTRAIN({{IFERROR(SORT(FILTER({ALL_},{B_}="Expense",{A_}>={START},{A_}<{END}),5,FALSE),{PAD6});{PAD}}},5,6)', bg="bg", fg="muted", size=9)
put(76, 39, "Latest entries", bg="bg", fg="muted", size=9, bold=True)
put(77, 39, f'=ARRAY_CONSTRAIN({{IFERROR(SORT(FILTER({ALL_},ISNUMBER({A_}),{B_}<>"Transfer"),1,FALSE),{PAD6});{PAD}}},5,6)', bg="bg", fg="muted", size=9)

# ------------------------------------------------------------------ page + header
fill(0, 0, LAST_ROW, 30, "bg")
put(1, 1, "Money Dashboard", bg="bg", fg="text", size=22, bold=True, span=(1, 14))
put(2, 1, f'=TEXT({START},"mmmm yyyy")&IF({TODAY_}=0,"  ·  upcoming",IF({LEFT}=0,"  ·  closed","  ·  day "&{TODAY_}&" of "&{DIM}&"  ·  "&{LEFT}&" days left"))',
    bg="bg", fg="muted", size=11, span=(2, 14))
put(1, 21, "MONTH", bg="bg", fg="muted", size=9, bold=True, span=(1, 24))
put(1, 26, "YEAR", bg="bg", fg="muted", size=9, bold=True, span=(1, 29))
import datetime
_now = datetime.date.today()
put(2, 21, _now.strftime("%B"), bg="card2", fg="blue", size=12, bold=True, ha="CENTER", span=(2, 24))
put(2, 26, _now.year, bg="card2", fg="blue", size=12, bold=True, ha="CENTER", span=(2, 29))
months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
dv = lambda vals: {"condition": {"type": "ONE_OF_LIST", "values": [{"userEnteredValue": str(v)} for v in vals]}, "strict": True, "showCustomUi": True}
extra.append({"setDataValidation": {"range": gr(2, 21, 2, 21), "rule": dv(months)}})
extra.append({"setDataValidation": {"range": gr(2, 26, 2, 26), "rule": dv([2025, 2026, 2027, 2028, 2029])}})

# ------------------------------------------------------------------ Layout C: answer first, then detail (Ink + Ice)
GOOD, WARN, BAD, ACC, MUTE = "#3E9C7A", "#C78A3A", "#C0494A", "#6D5BD0", "#B2ADCB"
def bigput(r, c, v, size, fg, nf, span):
    put(r, c, v, fg=fg, size=size, bold=True, nf=nf, span=span)
    cells[(r, c)][1]["numberFormat"]["pattern"] = '" "' + nf.replace("₱", "[$₱]")
    cells[(r, c)][1]["horizontalAlignment"] = "LEFT"

COLR = f'IF({USED}>1,"{BAD}",IF({USED}>{EXPECTED}+0.05,"{WARN}","{GOOD}"))'
STATUS = f'=IF({TODAY_}=0,"Upcoming month",IF({USED}>1,"⚠  Over budget",IF({USED}>{EXPECTED}+0.05,"▲  Spending ahead of pace",IF({LEFT}=0,"✓  Closed within plan","✓  On track"))))'
_win = f'{A_},">="&{START},{A_},"<"&{END}'

# --- hero: the answers
fill(4, 1, 11, 29, "card")
put(4, 1, "THIS MONTH AT A GLANCE", fg="muted", size=9, bold=True, span=(4, 14), pad=True)
put(4, 22, STATUS, bg="card2", fg="muted", size=10, bold=True, ha="CENTER", span=(4, 28))
HERO = [(1, 9, "SAFE TO SPEND / DAY", f"={SAFE}", "₱#,##0",
         f'=IF({LEFT}>0,"₱"&TEXT(MAX(0,{PLAN}-{SPENT}),"#,##0")&" of plan left  ·  "&{LEFT}&" days to go","Month complete")'),
        (11, 19, "LEFT THIS MONTH", f"={REMAIN}", "₱#,##0;-₱#,##0",
         f'="After ₱"&TEXT({SAVED},"#,##0")&" to savings  ·  spent ₱"&TEXT({SPENT},"#,##0")'),
        (21, 29, "BUDGET USED", f"={USED}", "0%",
         f'="Month is "&TEXT({EXPECTED},"0%")&" done  ·  plan ₱"&TEXT({PLAN},"#,##0")')]
for c0, c1, lab, val, nf, sub in HERO:
    put(5, c0, lab, fg="dim", size=9, bold=True, span=(5, c1), pad=True)
    bigput(6, c0, val, 36, "text", nf, (6, c1))
    put(7, c0, sub, fg="muted", size=10, span=(7, c1), pad=True)
put(9, 1, "Budget used", fg="dim", size=9, bold=True, span=(9, 5), pad=True)
put(9, 6, f'=SPARKLINE(MIN({USED},1),{{"charttype","bar";"max",1;"color1",{COLR}}})', span=(9, 28))
put(10, 1, "Month elapsed", fg="dim", size=9, bold=True, span=(10, 5), pad=True)
put(10, 6, f'=SPARKLINE(MIN({EXPECTED},1),{{"charttype","bar";"max",1;"color1","{MUTE}"}})', span=(10, 28))

# --- pace chart
fill(13, 1, 24, 29, "card")
put(13, 1, "SPENDING PACE  ·  cumulative vs plan", fg="muted", size=9, bold=True, span=(13, 14), pad=True)
put(13, 20, "● Actual   ● Plan pace  ", fg="muted", size=9, ha="RIGHT", span=(13, 29))
legend_runs = {(13, 20): [("● ", "blue"), ("Actual   ", "muted"), ("● ", "dim"), ("Plan pace  ", "muted")]}

# --- plan vs actual (left)
fill(26, 1, 39, 14, "card")
put(26, 1, "PLAN VS ACTUAL  ·  top 10 by spend", fg="muted", size=9, bold=True, span=(26, 14), pad=True)
for c, t, ha, sp in ((1, "CATEGORY", "LEFT", 5), (6, "PLANNED", "RIGHT", 7), (8, "ACTUAL", "RIGHT", 9), (10, "USED", "LEFT", 13), (14, "%", "RIGHT", 14)):
    put(27, c, t, bg="card2", fg="muted", size=9, bold=True, ha=ha, pad=(ha == "LEFT"), span=(27, sp))
fill(27, 1, 27, 14, "card2")
for i in range(10):
    r = 28 + i
    put(r, 1, f'=IFERROR(INDEX($AG$70:$AG$91,{i + 1})&"","")', fg="text", size=10, pad=True, span=(r, 5))
    put(r, 6, f'=IF($B{r + 1}="","",IF(N($AH${70 + i})=0,"—",$AH${70 + i}))', fg="muted", size=10, ha="RIGHT", nf="₱#,##0", span=(r, 7))
    put(r, 8, f'=IF($B{r + 1}="","",N($AI${70 + i}))', fg="text", size=10, bold=True, ha="RIGHT", nf="₱#,##0", span=(r, 9))
    bar = (f'=IF($B{r + 1}="","",SPARKLINE(IF(N($AH${70 + i})>0,MIN($AI${70 + i}/$AH${70 + i},1),1),'
           f'{{"charttype","bar";"max",1;"color1",IF(N($AH${70 + i})=0,"#A397DD",IF($AI${70 + i}>$AH${70 + i},"{BAD}",IF($AI${70 + i}>=0.8*$AH${70 + i},"{WARN}","{GOOD}")))}}))')
    put(r, 10, bar, fg="text", size=10, span=(r, 13))
    put(r, 14, f'=IF($B{r + 1}="","",IF(N($AH${70 + i})>0,$AI${70 + i}/$AH${70 + i},"new"))', fg="muted", size=10, ha="RIGHT", nf="0%")
put(38, 1, f'="Showing "&MIN(10,COUNT($AI$70:$AI$91))&" of "&COUNT($AI$70:$AI$91)&" active categories"', fg="dim", size=9, span=(38, 14), pad=True)

# --- savings goal (right, top)
fill(26, 16, 31, 29, "card")
put(26, 16, "SAVINGS GOAL  (edit name and target)", fg="muted", size=9, bold=True, span=(26, 29), pad=True)
put(27, 16, "Motorcycle fund", bg="card2", fg="blue", size=11, bold=True, span=(27, 21), pad=True)
put(27, 22, 0, bg="card2", fg="blue", size=11, bold=True, ha="RIGHT", nf="₱#,##0", span=(27, 29))
TGT = a1(27, 22, True)
put(28, 16, f'=SPARKLINE(IF(N({TGT})>0,MIN(MAX({SBAL},0)/{TGT},1),0),{{"charttype","bar";"max",1;"color1","{ACC}"}})', span=(28, 28))
put(29, 16, f'=IF(N({TGT})>0,"₱"&TEXT(MAX({SBAL},0),"#,##0")&" of ₱"&TEXT({TGT},"#,##0")&"  ·  "&TEXT(MAX({SBAL},0)/{TGT},"0%")&IF(AND({TGT}>{SBAL},{AVG3}>0),"  ·  about "&ROUNDUP(({TGT}-{SBAL})/{AVG3},0)&" months at your 3-month average",""),"Type a target amount above")', fg="muted", size=10, span=(29, 29), pad=True)
put(30, 16, f'="Savings balance ₱"&TEXT({SBAL},"#,##0")&"  ·  saved this month ₱"&TEXT({SAVED},"#,##0")', fg="dim", size=10, span=(30, 29), pad=True)

# --- debts (right, below goal)
fill(33, 16, 39, 29, "card")
put(33, 16, "DEBTS  ·  this month", fg="muted", size=9, bold=True, span=(33, 29), pad=True)
for c, t, ha, sp in ((16, "ACCOUNT", "LEFT", 20), (21, "CHARGED", "RIGHT", 24), (25, "PAID", "RIGHT", 29)):
    put(34, c, t, bg="card2", fg="muted", size=9, bold=True, ha=ha, pad=(ha == "LEFT"), span=(34, sp))
for i in range(4):
    r = 35 + i
    put(r, 16, f"=Budget!G{36 + i}", fg="text", size=10, span=(r, 20), pad=True)
    put(r, 21, f'=SUMIFS({E_},{C_},$Q{r + 1},{B_},"Expense",{_win})', fg="text", size=10, ha="RIGHT", nf="₱#,##0", span=(r, 24))
    put(r, 25, f'=SUMIFS({E_},{F_},"To: "&$Q{r + 1}&"*",{_win})', fg="text", size=10, ha="RIGHT", nf="₱#,##0", span=(r, 29))

# --- activity + 6-month chart (bottom row of three cards)
for (c0, c1) in ((1, 9), (11, 19), (21, 29)): fill(41, c0, 47, c1, "card")
put(41, 1, "BIGGEST EXPENSES", fg="muted", size=9, bold=True, span=(41, 9), pad=True)
put(41, 11, "LATEST ENTRIES", fg="muted", size=9, bold=True, span=(41, 19), pad=True)
put(41, 21, "INCOME VS SPENT  ·  last 6 months", fg="muted", size=9, bold=True, span=(41, 29), pad=True)
for c0, hdr in ((1, 70), (11, 78)):
    for i in range(5):
        r, row = 42 + i, hdr + i
        put(r, c0, f'=IFERROR(TEXT($AN${row},"mmm d"),"")', fg="muted", size=10, span=(r, c0 + 1), pad=True)
        put(r, c0 + 2, f'=IFERROR($AQ${row}&IF($AS${row}="Untitled Transaction",""," · "&$AS${row}),"")', fg="text", size=10, span=(r, c0 + 6))
        put(r, c0 + 7, f'=IFERROR($AR${row},"")', fg="text", size=10, bold=True, ha="RIGHT", nf="₱#,##0", span=(r, c0 + 8))

# --- stats strip
fill(49, 1, 51, 29, "card")
DEBTF = (f'=SUMPRODUCT(SUMIFS({E_},{C_},Budget!$G$36:$G$39,{B_},"Expense",{_win}))'
         f'-SUMPRODUCT(SUMIFS({E_},{F_},"To: "&Budget!$G$36:$G$39&"*",{_win}))')
strip = [("INCOME", f"={INC}", "green"), ("SPENT", f"={SPENT}", "orange"), ("SAVED THIS MONTH", f"={SAVED}", "blue"),
         ("NEW DEBT", DEBTF, "red"), ("OWED TO YOU", f"={RECV}", "blue"), ("SAVINGS BALANCE", f"={SBAL}", "green")]
for g, (lab, f, colr) in enumerate(strip):
    put(49, gs(g), lab, fg="dim", size=9, bold=True, span=(49, ge(g)), pad=True)
    bigput(50, gs(g), f, 16, colr, "₱#,##0;-₱#,##0", (50, ge(g)))

# ------------------------------------------------------------------ conditional formatting (Dashboard)
def cf(rng, formula, fg=None, bg=None):
    fmt = {}
    if fg: fmt["textFormat"] = {"foregroundColor": C[fg], "bold": True}
    if bg: fmt["backgroundColor"] = C[bg]
    cfs.append({"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [rng],
        "booleanRule": {"condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": formula}]}, "format": fmt}}}})
for rng, tint in ((gr(4, 22, 4, 28), True), (gr(6, 21, 6, 29), False)):   # status chip + budget-used number
    cf(rng, f"={USED}<={EXPECTED}+0.05", fg="green", bg="gt" if tint else None)
    cf(rng, f"={USED}>{EXPECTED}+0.05", fg="orange", bg="wt" if tint else None)
    cf(rng, f"={USED}>1", fg="red", bg="rt" if tint else None)
cf(gr(6, 1, 6, 9), f"={SAFE}<=0", fg="red")
cf(gr(6, 1, 6, 9), f"={SAFE}>0", fg="green")
cf(gr(6, 11, 6, 19), f"={REMAIN}<0", fg="red")
cf(gr(28, 14, 37, 14), "=AND(ISNUMBER($O29),$O29>=0.8)", fg="orange")
cf(gr(28, 14, 37, 14), "=AND(ISNUMBER($O29),$O29>1)", fg="red")

# ------------------------------------------------------------------ charts
def src(r0, c0, r1, c1): return {"sourceRange": {"sources": [gr(r0, c0, r1, c1)]}}
def axis_fmt(): return {"foregroundColor": C["muted"], "foregroundColorStyle": {"rgbColor": C["muted"]}, "fontSize": 9, "fontFamily": "Roboto", "bold": False}
def chart(cid, spec, r, c, w, h, ox=8, oy=4):
    spec.update({"backgroundColorStyle": {"rgbColor": C["card"]}, "hiddenDimensionStrategy": "SHOW_ALL", "fontName": "Roboto"})
    charts.append({"addChart": {"chart": {"chartId": cid, "spec": spec, "position": {"overlayPosition": {
        "anchorCell": {"sheetId": DASH, "rowIndex": r, "columnIndex": c}, "offsetXPixels": ox, "offsetYPixels": oy, "widthPixels": w, "heightPixels": h}}}}})
chart(910001, {"basicChart": {"chartType": "LINE", "legendPosition": "NO_LEGEND", "headerCount": 1,
    "axis": [{"position": "BOTTOM_AXIS", "format": axis_fmt()}, {"position": "LEFT_AXIS", "format": axis_fmt()}],
    "domains": [{"domain": src(34, 32, 65, 32)}],
    "series": [{"series": src(34, 33, 65, 33), "targetAxis": "LEFT_AXIS", "color": C["blue"], "colorStyle": {"rgbColor": C["blue"]}, "lineStyle": {"width": 3, "type": "SOLID"}},
               {"series": src(34, 34, 65, 34), "targetAxis": "LEFT_AXIS", "color": C["dim"], "colorStyle": {"rgbColor": C["dim"]}, "lineStyle": {"width": 2, "type": "MEDIUM_DASHED"}}]}},
      14, 1, 1088, 218, ox=8, oy=2)
chart(910003, {"basicChart": {"chartType": "COLUMN", "legendPosition": "NO_LEGEND", "headerCount": 1,
    "axis": [{"position": "BOTTOM_AXIS", "format": axis_fmt()}, {"position": "LEFT_AXIS", "format": axis_fmt()}],
    "domains": [{"domain": src(26, 32, 32, 32)}],
    "series": [{"series": src(26, 33, 32, 33), "targetAxis": "LEFT_AXIS", "colorStyle": {"rgbColor": C["green"]}},
               {"series": src(26, 34, 32, 34), "targetAxis": "LEFT_AXIS", "colorStyle": {"rgbColor": C["blue"]}}]}},
      42, 21, 356, 142, ox=4, oy=2)

# ------------------------------------------------------------------ assemble Dashboard requests
def dashboard_requests(show_helpers=False):
    reqs = [{"addSheet": {"properties": {"sheetId": DASH, "title": "Dashboard", "index": 0,
        "gridProperties": {"rowCount": 100, "columnCount": 52, "hideGridlines": True}, "tabColorStyle": {"rgbColor": C["blue"]}}}},
        {"repeatCell": {"range": gr(0, 0, 99, 51), "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Roboto"}}}, "fields": "userEnteredFormat.textFormat.fontFamily"}}]
    widths2 = []
    for a, b, px in widths:
        if widths2 and widths2[-1][1] == a and widths2[-1][2] == px: widths2[-1] = (widths2[-1][0], b, px)
        else: widths2.append((a, b, px))
    for a, b, px in widths2:
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": DASH, "dimension": "COLUMNS", "startIndex": a, "endIndex": b}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    rs = sorted(ROWS); i = 0
    while i < len(rs):
        j = i
        while j + 1 < len(rs) and rs[j + 1] == rs[j] + 1 and ROWS[rs[j + 1]] == ROWS[rs[i]]: j += 1
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": DASH, "dimension": "ROWS", "startIndex": rs[i], "endIndex": rs[j] + 1}, "properties": {"pixelSize": ROWS[rs[i]]}, "fields": "pixelSize"}})
        i = j + 1
    pal, copy, clean = emit_formats()
    reqs += fills + pal + copy + clean + merges + emit_values() + extra
    for (r, c), runs in legend_runs.items():          # coloured bullets for the chart captions
        text = "".join(t for t, _ in runs); pos = 0; fr = []
        for t, colr in runs:
            fr.append({"startIndex": pos, "format": {"foregroundColor": C[colr]}}); pos += len(t)
        reqs.append({"updateCells": {"start": {"sheetId": DASH, "rowIndex": r, "columnIndex": c}, "rows": [{"values": [{"userEnteredValue": {"stringValue": text}, "textFormatRuns": fr}]}], "fields": "userEnteredValue,textFormatRuns"}})
    reqs += cfs + charts
    if not show_helpers:
        reqs.append({"updateDimensionProperties": {"range": {"sheetId": DASH, "dimension": "COLUMNS", "startIndex": 31, "endIndex": 52}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
    return reqs

# ------------------------------------------------------------------ recolour the other tabs (light palette)
def rc(sid, r0, c0, r1, c1, bg=None, fg=None, bold=None, size=None):
    fmt, fields = {}, []
    if bg: fmt["backgroundColor"] = C[bg]; fields.append("userEnteredFormat.backgroundColor")
    tf = {}
    if fg: tf["foregroundColor"] = C[fg]; fields.append("userEnteredFormat.textFormat.foregroundColor")
    if bold is not None: tf["bold"] = bold; fields.append("userEnteredFormat.textFormat.bold")
    if size: tf["fontSize"] = size; fields.append("userEnteredFormat.textFormat.fontSize")
    if tf: fmt["textFormat"] = tf
    return {"repeatCell": {"range": gr(r0, c0, r1, c1, sid), "cell": {"userEnteredFormat": fmt}, "fields": ",".join(fields)}}

def border(sid, r0, c0, r1, c1, color="rule", top=False):
    b = {"style": "SOLID", "colorStyle": {"rgbColor": C[color]}}
    d = {"range": gr(r0, c0, r1, c1, sid), "bottom": b, "innerHorizontal": b}
    if top: d["top"] = {"style": "SOLID", "colorStyle": {"rgbColor": C["ink"]}}
    return {"updateBorders": d}

def other_tabs():
    q = []
    q.append({"updateSpreadsheetProperties": {"properties": {"spreadsheetTheme": {"primaryFontFamily": "Roboto", "themeColors": [
        {"colorType": t, "color": {"rgbColor": C[k]}} for t, k in (("TEXT", "body"), ("BACKGROUND", "white"), ("ACCENT1", "blue"), ("ACCENT2", "teal"), ("ACCENT3", "purple"),
                                                                  ("ACCENT4", "orange"), ("ACCENT5", "pink"), ("ACCENT6", "green"), ("LINK", "b_h"))]}}, "fields": "spreadsheetTheme"}})
    # ---- Budget
    q += [rc(BUDGET, 0, 0, 44, 14, bg="page")]
    q += [rc(BUDGET, 0, 0, 0, 14, bg="ink", fg="white", bold=True, size=16)]
    blocks = [  # (header rows range, colhdr range, body range, total range, header colour, tint)
        ((16, 2, 16, 4), (17, 2, 17, 4), (18, 2, 31, 4), (32, 2, 32, 4), "g_h", "g_t"),
        ((16, 6, 16, 8), (17, 6, 17, 8), (18, 6, 25, 8), (26, 6, 26, 8), "b_h", "b_t"),
        ((16, 10, 16, 12), (17, 10, 17, 12), (18, 10, 31, 12), (32, 10, 32, 12), "p_h", "p_t"),
        ((28, 6, 28, 8), (29, 6, 29, 8), (30, 6, 30, 8), (31, 6, 31, 8), "t_h", "t_t"),
        ((33, 6, 33, 8), (34, 6, 34, 8), (35, 6, 38, 8), (39, 6, 39, 8), "r_h", "r_t"),
        ((34, 2, 34, 4), (36, 2, 36, 4), (37, 2, 41, 4), (42, 2, 42, 4), "ink", "s_t"),
    ]
    for hd, ch, body, tot, hc, tint in blocks:
        q += [rc(BUDGET, *hd, bg=hc, fg="white", bold=True), rc(BUDGET, *ch, bg=tint, fg="body", bold=True),
              rc(BUDGET, *body, bg="white", fg="body"), border(BUDGET, *body), rc(BUDGET, *tot, bg=tint, fg="body", bold=True)]
    # month / year now follow the Dashboard pickers
    q.append({"setDataValidation": {"range": gr(13, 3, 14, 3, BUDGET)}})
    q.append({"updateCells": {"start": {"sheetId": BUDGET, "rowIndex": 13, "columnIndex": 2}, "rows": [
        {"values": [{"userEnteredValue": {"stringValue": "MONTH  (set on Dashboard)"}}, {"userEnteredValue": {"formulaValue": "=Dashboard!V3"}}]},
        {"values": [{"userEnteredValue": {"stringValue": "YEAR  (set on Dashboard)"}}, {"userEnteredValue": {"formulaValue": "=Dashboard!AA3"}}]}], "fields": "userEnteredValue"}})
    q += [rc(BUDGET, 13, 2, 14, 2, bg="page", fg="dim", bold=True, size=9), rc(BUDGET, 13, 3, 14, 3, bg="s_t", fg="body", bold=True)]
    # old charts are replaced by the Dashboard
    for cid in (1207681199, 2103266406): q.append({"deleteEmbeddedObject": {"objectId": cid}})
    q.append({"updateDimensionProperties": {"range": {"sheetId": BUDGET, "dimension": "ROWS", "startIndex": 0, "endIndex": 1}, "properties": {"pixelSize": 44}, "fields": "pixelSize"}})
    q.append({"updateDimensionProperties": {"range": {"sheetId": BUDGET, "dimension": "ROWS", "startIndex": 1, "endIndex": 13}, "properties": {"hiddenByUser": True}, "fields": "hiddenByUser"}})
    q.append({"updateSheetProperties": {"properties": {"sheetId": BUDGET, "index": 1, "tabColorStyle": {"rgbColor": C["g_h"]}}, "fields": "index,tabColorStyle"}})
    # ---- Transactions Log (ranges start at row 2 so the sync's insert-at-row-3 keeps them intact)
    q += [rc(LOG, 0, 0, 0, 5, bg="ink", fg="white", bold=True, size=16), rc(LOG, 1, 0, 1, 5, bg="acc", fg="white", bold=True, size=10),
          rc(LOG, 2, 0, 4368, 5, fg="body", size=10), border(LOG, 2, 0, 4368, 5)]
    q.append({"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [gr(1, 0, 4368, 5, LOG)], "booleanRule": {
        "condition": {"type": "CUSTOM_FORMULA", "values": [{"userEnteredValue": "=AND(ROW()>2,ISEVEN(ROW()))"}]}, "format": {"backgroundColor": C["zebra"]}}}}})
    for ty, colr in (("Income", "g_h"), ("Expense", "r_h"), ("Transfer", "dim")):
        cfs_log = {"addConditionalFormatRule": {"index": 0, "rule": {"ranges": [gr(1, 1, 4368, 1, LOG)], "booleanRule": {
            "condition": {"type": "TEXT_EQ", "values": [{"userEnteredValue": ty}]}, "format": {"textFormat": {"foregroundColor": C[colr], "bold": True}}}}}}
        q.append(cfs_log)
    q.append({"repeatCell": {"range": gr(2, 4, 4368, 4, LOG), "cell": {"userEnteredFormat": {"numberFormat": {"type": "NUMBER", "pattern": "#,##0.00"}, "horizontalAlignment": "RIGHT"}}, "fields": "userEnteredFormat.numberFormat,userEnteredFormat.horizontalAlignment"}})
    q.append({"repeatCell": {"range": gr(1, 0, 4368, 5, LOG), "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE"}}, "fields": "userEnteredFormat.verticalAlignment"}})
    for a, b, px in ((0, 1, 44), (1, 2, 32), (2, 4369, 26)):
        q.append({"updateDimensionProperties": {"range": {"sheetId": LOG, "dimension": "ROWS", "startIndex": a, "endIndex": b}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    q.append({"updateSheetProperties": {"properties": {"sheetId": LOG, "tabColorStyle": {"rgbColor": C["ink"]}}, "fields": "tabColorStyle"}})
    # ---- Setup
    q.append({"updateSheetProperties": {"properties": {"sheetId": SETUP, "tabColorStyle": {"rgbColor": C["dim"]}}, "fields": "tabColorStyle"}})
    # ---- Wishlist
    q += [rc(WISH, 0, 0, 0, 12, bg="ink", fg="white", bold=True, size=16), rc(WISH, 1, 2, 1, 8, bg="acc", fg="white", bold=True),
          rc(WISH, 2, 10, 2, 11, bg="p_t", fg="body", bold=True)]
    q.append({"updateSheetProperties": {"properties": {"sheetId": WISH, "tabColorStyle": {"rgbColor": C["p_h"]}}, "fields": "tabColorStyle"}})
    return q

def budget_polish():
    """Formatting only (font, alignment, number format, borders, row heights). No values, formulas or colours are touched."""
    line = {"red": 0.80, "green": 0.77, "blue": 0.89}        # soft lavender-grey for grid lines
    edge = {"red": 0.55, "green": 0.50, "blue": 0.72}        # a bit darker for block outlines
    none = {"style": "NONE"}
    thin = lambda c: {"style": "SOLID", "color": c}
    q = []
    # one font for the whole tab
    q.append({"repeatCell": {"range": gr(0, 0, 60, 14, BUDGET), "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Roboto"}}}, "fields": "userEnteredFormat.textFormat.fontFamily"}})
    # (c0, header row, column-header row, first body row, last body row, total row, number-formatted?)
    blocks = [(2, 16, 17, 18, 31, 32, True), (6, 16, 17, 18, 25, 26, True), (10, 16, 17, 18, 31, 32, True),
              (6, 28, 29, 30, 30, 31, True), (6, 33, 34, 35, 38, 39, False), (2, 34, 36, 37, 41, 42, False)]
    for c0, hd, ch, b0, b1, tot, money in blocks:
        c1 = c0 + 2
        q.append({"updateBorders": {"range": gr(hd, c0, tot, c1, BUDGET), "top": none, "bottom": none, "left": none, "right": none, "innerHorizontal": none, "innerVertical": none}})
        q.append({"repeatCell": {"range": gr(hd, c0, tot, c1, BUDGET), "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10, "bold": False}}},
                                 "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.bold"}})
        q.append({"repeatCell": {"range": gr(hd, c0, hd, c1, BUDGET), "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER", "textFormat": {"fontSize": 11, "bold": True}}},
                                 "fields": "userEnteredFormat.horizontalAlignment,userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.bold"}})
        q.append({"repeatCell": {"range": gr(ch, c0, ch, c1, BUDGET), "cell": {"userEnteredFormat": {"horizontalAlignment": "CENTER", "textFormat": {"fontSize": 9, "bold": True}}},
                                 "fields": "userEnteredFormat.horizontalAlignment,userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.bold"}})
        q.append({"repeatCell": {"range": gr(b0, c0, tot, c0, BUDGET), "cell": {"userEnteredFormat": {"horizontalAlignment": "LEFT"}}, "fields": "userEnteredFormat.horizontalAlignment"}})
        nf = {"numberFormat": {"type": "CURRENCY", "pattern": "[$\u20b1]#,##0.00"}} if money else {}
        q.append({"repeatCell": {"range": gr(b0, c0 + 1, tot, c1, BUDGET), "cell": {"userEnteredFormat": {"horizontalAlignment": "RIGHT", **nf}},
                                 "fields": "userEnteredFormat.horizontalAlignment" + (",userEnteredFormat.numberFormat" if money else "")}})
        q.append({"repeatCell": {"range": gr(tot, c0, tot, c1, BUDGET), "cell": {"userEnteredFormat": {"textFormat": {"bold": True}}}, "fields": "userEnteredFormat.textFormat.bold"}})
        q.append({"updateBorders": {"range": gr(ch, c0, tot, c1, BUDGET), "innerHorizontal": thin(line), "bottom": thin(edge), "left": thin(edge), "right": thin(edge)}})
        q.append({"updateBorders": {"range": gr(ch, c0, ch, c1, BUDGET), "bottom": thin(edge)}})
        q.append({"updateBorders": {"range": gr(tot, c0, tot, c1, BUDGET), "top": thin(edge)}})
    # month / year box
    q.append({"repeatCell": {"range": gr(13, 2, 14, 3, BUDGET), "cell": {"userEnteredFormat": {"verticalAlignment": "MIDDLE", "textFormat": {"fontSize": 10, "bold": True}}},
                             "fields": "userEnteredFormat.verticalAlignment,userEnteredFormat.textFormat.fontSize,userEnteredFormat.textFormat.bold"}})
    q.append({"updateBorders": {"range": gr(13, 2, 14, 3, BUDGET), "top": none, "bottom": none, "left": none, "right": none, "innerHorizontal": none, "innerVertical": none}})
    q.append({"updateBorders": {"range": gr(13, 2, 14, 3, BUDGET), "top": thin(edge), "bottom": thin(edge), "left": thin(edge), "right": thin(edge), "innerHorizontal": thin(line), "innerVertical": thin(line)}})
    # row heights
    for a, b, px in ((13, 15, 28), (15, 16, 14), (16, 17, 32), (17, 43, 28)):
        q.append({"updateDimensionProperties": {"range": {"sheetId": BUDGET, "dimension": "ROWS", "startIndex": a, "endIndex": b}, "properties": {"pixelSize": px}, "fields": "pixelSize"}})
    return q

def setup_table():
    return [{"updateTable": {"table": {"tableId": "1227669263", "rowsProperties": {"headerColorStyle": {"rgbColor": C["ink"]},
        "firstBandColorStyle": {"rgbColor": C["white"]}, "secondBandColorStyle": {"rgbColor": C["zebra"]}}}, "fields": "rowsProperties"}}]

# ------------------------------------------------------------------ main
def main():
    show = "--show-helpers" in sys.argv
    d, o = dashboard_requests(show), other_tabs()
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")
    os.makedirs(out, exist_ok=True)
    for name, reqs in (("dashboard_requests", d), ("tabs_requests", o), ("setup_table_requests", setup_table())):
        with open(os.path.join(out, name + ".json"), "w", encoding="utf-8") as fh:
            fh.write("[\n" + ",\n".join(json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in reqs) + "\n]\n")
    print(f"dashboard: {len(d)} requests, other tabs: {len(o)} requests -> {os.path.normpath(out)}")
    if "--apply" not in sys.argv:
        print("dry run only; add --apply to write to the sheet")
        return
    # self-contained auth (does not import the sync script, so pandas is not needed)
    import gspread
    from dotenv import load_dotenv
    root = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
    load_dotenv(os.path.join(root, ".env"))
    key = os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "")
    key = key if os.path.isabs(key) else os.path.join(root, key)
    from gspread.exceptions import APIError
    client = gspread.service_account(filename=key)
    sh = client.open_by_key(os.getenv("GOOGLE_SHEETS_SPREADSHEET_ID") or SS_ID)
    keep, old = {}, None
    for ws in sh.worksheets():
        if ws.id == DASH: old = ws
    def send(reqs, label):
        try:
            sh.batch_update({"requests": reqs})
            print("ok:", label)
        except APIError as e:
            print("FAILED:", label, "\n", e.response.text[:1500]); raise SystemExit(1)
    if "--budget-only" in sys.argv:        # formatting polish for the Budget tab only; nothing else is touched
        send(budget_polish(), "Budget tab: one font, even borders, aligned numbers")
        print("done."); return
    tabs_only = "--tabs-only" in sys.argv
    if old is not None and not tabs_only:
        keep = {"month": old.acell("V3").value, "year": old.acell("AA3").value, "goal": old.acell("Q32").value or old.acell("Q28").value or old.acell("B45").value, "target": old.acell("W32").value or old.acell("W28").value or old.acell("G45").value}
        send([{"deleteSheet": {"sheetId": DASH}}], "removed the old Dashboard (inputs kept: %s)" % keep)
    if not tabs_only:
        send(d, "Dashboard")
    else:
        print("skipped: Dashboard left untouched (--tabs-only)")
    if keep:   # carry the user's picks over to the fresh tab
        def num(v):
            try: return {"numberValue": float(str(v).replace(",", "").replace("\u20b1", ""))}
            except ValueError: return {}
        spots = [(2, 21, {"stringValue": keep["month"]} if keep["month"] else {}), (2, 26, num(keep["year"])),
                 (27, 16, {"stringValue": keep["goal"].strip()} if keep["goal"] else {}), (27, 22, num(keep["target"]))]
        rest = [{"updateCells": {"start": {"sheetId": DASH, "rowIndex": r, "columnIndex": c}, "rows": [{"values": [{"userEnteredValue": v}]}], "fields": "userEnteredValue"}}
                for r, c, v in spots if v]
        if rest: send(rest, "restored month / year / goal")
    meta = sh.fetch_sheet_metadata()
    live = {c["chartId"] for w in meta.get("sheets", []) for c in w.get("charts", [])}
    o = [r for r in o if "deleteEmbeddedObject" not in r or r["deleteEmbeddedObject"]["objectId"] in live]
    n_old = next((len(w.get("conditionalFormats", [])) for w in meta.get("sheets", []) if w["properties"]["sheetId"] == LOG), 0)
    o = [{"deleteConditionalFormatRule": {"sheetId": LOG, "index": 0}} for _ in range(n_old)] + o   # no stacked duplicates across runs
    send(o, "Budget, Log, Setup, Wishlist colours + picker links")
    try:
        sh.batch_update({"requests": setup_table()}); print("ok: Setup table colours")
    except APIError as e:
        print("skipped Setup table colours:", e)
    print("done. Open the Dashboard tab.")

if __name__ == "__main__":
    main()
