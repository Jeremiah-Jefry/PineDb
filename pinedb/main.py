#!/usr/bin/env python3
"""PineDB v1.0 — interactive REPL"""
import sys
from pathlib import Path
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

    recovered = wal.recover(pager)
    if recovered > 0:
        print(f"[recovery] replayed {recovered} page(s) from WAL")

    txn_mgr = TransactionManager(pager, wal)
    executor = Executor(pager, wal, txn_mgr)

    print("PineDB v1.0  —  type SQL or EXIT")
    while True:
        try:
            line = input("pinedb> ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        if line.upper() in ("EXIT", "QUIT", "\\Q"):
            break
        try:
            ast = Parser(line).parse()
            result = executor.execute(ast)
            if isinstance(result, list):
                if not result:
                    print("(0 rows)")
                for row in result:
                    print(row)
            else:
                print(result)
        except ParseError as e:
            print(f"syntax error: {e}")
        except Exception as e:
            import traceback; traceback.print_exc()
            print(f"error: {e}")

    pager.close()
    wal.close()
    print("bye.")

if __name__ == "__main__":
    main()
