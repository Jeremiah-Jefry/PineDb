#!/usr/bin/env python3
"""PineDB REPL — Layer 8"""

import sys
from pathlib import Path

# Fix python path if running directly from src folder
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pinedb.pager import Pager
from pinedb.wal import WAL
from pinedb.txn import TransactionManager
from pinedb.executor import Executor
from pinedb.parser import Parser, ParseError

def main():
    db_path = sys.argv[1] if len(sys.argv) > 1 else "data/pinedb.db"
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

    pager = Pager(db_path)
    wal = WAL(db_path, pager)

    # Recovery on startup — always run this first
    recovered = wal.recover(pager)
    if recovered:
        print(f"[recovery] replayed {recovered} pages from WAL")

    txn_mgr = TransactionManager(pager, wal)
    executor = Executor(pager, wal, txn_mgr)

    print("PineDB v1.0  —  type SQL or EXIT")
    while True:
        try:
            line = input("pinedb> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
            
        if not line:
            continue
            
        if line.upper() in ("EXIT", "QUIT", "\\Q"):
            break
            
        try:
            ast = Parser(line).parse()
            result = executor.execute(ast)
            
            if isinstance(result, list):
                for row in result:
                    print(row)
            else:
                print(result)
                
        except ParseError as e:
            print(f"parse error: {e}")
        except Exception as e:
            import traceback
            traceback.print_exc()

    pager.close()
    print("bye.")

if __name__ == "__main__":
    main()
