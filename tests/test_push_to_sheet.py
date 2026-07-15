import pandas as pd
import pytest

import money_manager_to_notion as m


class FakeWorksheet:
    title = "Transactions Log"

    def __init__(self, existing_rows, fail_times=0):
        self._existing_rows = [[], []] + existing_rows
        self.inserted = None
        self.fail_times = fail_times
        self.insert_calls = 0

    def get_all_values(self):
        return self._existing_rows

    def insert_rows(self, rows, row, value_input_option):
        self.insert_calls += 1
        if self.insert_calls <= self.fail_times:
            raise ConnectionError("transient")
        self.inserted = rows


def _clean_df():
    return pd.DataFrame([{
        "date": pd.Timestamp("2026-07-15 17:11", tz="Asia/Manila"),
        "type": "Expense",
        "account_name": "GCash",
        "category_name": "Transport",
        "amount": 61.0,
        "note": "moveit",
    }])


def test_pushes_new_row_when_not_already_present(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    worksheet = FakeWorksheet(existing_rows=[])

    result = m.push_to_sheet(worksheet, _clean_df())

    assert result is True
    assert worksheet.inserted == [["2026-07-15 17:11", "Expense", "GCash", "Transport", 61.0, "moveit"]]


def test_skips_push_when_row_already_present(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    worksheet = FakeWorksheet(existing_rows=[
        ["2026-07-15 17:11", "Expense", "GCash", "Transport", "61", "moveit"],
    ])

    result = m.push_to_sheet(worksheet, _clean_df())

    assert result is True
    assert worksheet.inserted is None


def test_retries_transient_insert_failure_then_succeeds(monkeypatch):
    monkeypatch.setattr(m.time, "sleep", lambda *_: None)
    worksheet = FakeWorksheet(existing_rows=[], fail_times=2)

    result = m.push_to_sheet(worksheet, _clean_df())

    assert result is True
    assert worksheet.insert_calls == 3
    assert worksheet.inserted is not None
