import sqlite3
from pathlib import Path
p = Path('logs/agent_logs.db')
print('exists:', p.exists())
if p.exists():
    conn = sqlite3.connect(str(p))
    cur = conn.cursor()
    for row in cur.execute('SELECT id, timestamp, invoice_number, agent, status, decision, flags, total, vendor FROM agent_logs ORDER BY id'):
        print(row)
    conn.close()
else:
    print('no logs DB')
