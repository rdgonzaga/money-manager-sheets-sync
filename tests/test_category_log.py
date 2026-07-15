import money_manager_to_notion as m


def test_writes_new_categories_to_file(tmp_path):
    log_file = tmp_path / "unbucketed_categories.log"

    m.log_new_categories(["Gym", "Bus"], filename=str(log_file))

    assert log_file.read_text().splitlines() == ["Gym", "Bus"]


def test_does_not_duplicate_already_logged_categories(tmp_path):
    log_file = tmp_path / "unbucketed_categories.log"
    log_file.write_text("Gym\n")

    m.log_new_categories(["Gym", "Bus"], filename=str(log_file))

    assert log_file.read_text().splitlines() == ["Gym", "Bus"]


def test_no_op_on_empty_list(tmp_path):
    log_file = tmp_path / "unbucketed_categories.log"

    result = m.log_new_categories([], filename=str(log_file))

    assert result is True
    assert not log_file.exists()
