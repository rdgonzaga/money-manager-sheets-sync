from money_manager_to_notion import filter_duplicate_rows


def test_filters_out_exact_match():
    rows = [["2026-07-15 17:11", "Expense", "GCash", "Transport", 61.0, "moveit"]]
    existing = [["2026-07-15 17:11", "Expense", "GCash", "Transport", "61", "moveit"]]

    result = filter_duplicate_rows(rows, existing)

    assert result == []


def test_keeps_rows_not_already_present():
    rows = [["2026-07-15 17:11", "Expense", "GCash", "Transport", 61.0, "moveit"]]
    existing = [["2026-07-14 09:00", "Income", "Cash", "Allowance", "500", "Untitled Transaction"]]

    result = filter_duplicate_rows(rows, existing)

    assert result == rows


def test_matches_amount_regardless_of_decimal_formatting():
    rows = [["2026-07-15 17:11", "Expense", "GCash", "Transport", 61.0, "moveit"]]
    existing = [["2026-07-15 17:11", "Expense", "GCash", "Transport", "61.00", "moveit"]]

    result = filter_duplicate_rows(rows, existing)

    assert result == []


def test_ignores_malformed_existing_rows():
    rows = [["2026-07-15 17:11", "Expense", "GCash", "Transport", 61.0, "moveit"]]
    existing = [[], ["DATE", "TYPE", "ACCOUNT", "CATEGORY", "AMOUNT", "DETAILS / NAME"], ["", "", "", "", "", ""]]

    result = filter_duplicate_rows(rows, existing)

    assert result == rows
