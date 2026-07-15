import pandas as pd

from money_manager_to_notion import transform_data


def _raw_row(**overrides):
    row = {
        "timestamp": 700000000.0,
        "type": "1",
        "amount": -150.0,
        "account_name": "Cash",
        "to_account_name": None,
        "category_name": "Food",
        "note": "Lunch",
    }
    row.update(overrides)
    return pd.DataFrame([row])


def test_null_category_becomes_uncategorized_string():
    df = transform_data(_raw_row(category_name=None))

    assert df.loc[0, "category_name"] == "Uncategorized"
    assert isinstance(df.loc[0, "category_name"], str)


def test_null_account_becomes_unknown_account_string():
    df = transform_data(_raw_row(account_name=None))

    assert df.loc[0, "account_name"] == "Unknown Account"
    assert isinstance(df.loc[0, "account_name"], str)


def test_null_note_becomes_untitled_transaction_string():
    df = transform_data(_raw_row(note=None))

    assert df.loc[0, "note"] == "Untitled Transaction"
    assert isinstance(df.loc[0, "note"], str)


def test_no_nan_survives_in_string_columns_for_all_null_row():
    df = transform_data(_raw_row(category_name=None, account_name=None, note=None))

    for column in ("category_name", "account_name", "note"):
        assert df[column].notna().all()
        assert all(isinstance(v, str) for v in df[column])


def test_transfer_row_enriches_note_and_forces_transfer_category():
    df = transform_data(_raw_row(type="3", category_name="Food", to_account_name="GCash", note="Savings"))

    assert df.loc[0, "category_name"] == "Transfer"
    assert df.loc[0, "note"] == "To: GCash | Savings"


def test_amount_is_always_positive():
    df = transform_data(_raw_row(amount=-150.0))

    assert df.loc[0, "amount"] == 150.0
