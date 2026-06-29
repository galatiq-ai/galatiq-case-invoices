import sqlite3

# Create (or open) the SQLite database file
connection = sqlite3.connect("inventory.db")

# Create a cursor to execute SQL commands
cursor = connection.cursor()

# Create the inventory table if it doesn't already exist
cursor.execute("""
CREATE TABLE IF NOT EXISTS inventory (
    item TEXT PRIMARY KEY,
    stock INTEGER
)
""")

# Insert sample inventory data
inventory_data = [
    ("WidgetA", 15),
    ("WidgetB", 10),
    ("GadgetX", 5),
    ("FakeItem", 0)
]

cursor.executemany("""
INSERT OR REPLACE INTO inventory (item, stock)
VALUES (?, ?)
""", inventory_data)

# Save the changes
connection.commit()

# Close the database connection
connection.close()

print("Inventory database created successfully!")