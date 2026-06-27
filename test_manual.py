import sys
import subprocess
import os

if os.path.exists('data/demo.db'):
    os.remove('data/demo.db')
if os.path.exists('data/demo.db.wal'):
    os.remove('data/demo.db.wal')

cmds = [
    "CREATE TABLE t (id INT, name VARCHAR);",
    "INSERT INTO t VALUES (1, 'alice');",
    "INSERT INTO t VALUES (2, 'bob');",
    "SELECT * FROM t WHERE id = 1;",
    "EXIT"
]

p = subprocess.Popen(['python', 'pinedb/main.py', 'data/demo.db'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
out, err = p.communicate('\n'.join(cmds) + '\n')
print(out)
if err:
    print("ERR:", err)
