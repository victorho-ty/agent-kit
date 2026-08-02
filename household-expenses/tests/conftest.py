import pytest

from expense_tracker import db


@pytest.fixture
def conn(tmp_path):
    connection = db.connect(tmp_path / "expenses.db")
    yield connection
    connection.close()
