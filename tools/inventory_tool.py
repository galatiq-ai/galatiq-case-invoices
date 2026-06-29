import sqlite3
from typing import Optional


class InventoryTool:
    def __init__(self, db_path: str = "inventory.db"):
        self.db_path = db_path

    def get_stock(self, item_name: str) -> Optional[int]:
        connection = sqlite3.connect(self.db_path)
        cursor = connection.cursor()

        cursor.execute(
            "SELECT stock FROM inventory WHERE item = ?",
            (item_name,)
        )

        row = cursor.fetchone()
        connection.close()

        if row is None:
            return None

        return row[0]