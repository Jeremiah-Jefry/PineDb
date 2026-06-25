import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from src.pager import Pager
from src.record import encode, decode

DB_PATH = "data/test.db"

def run_test():
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)

    pointers = []
    pager = Pager(DB_PATH)
    for i in range(1000):
        ptr = pager.insert_record(encode(i, f"user{i}"))
        pointers.append(ptr)
    pager.close()

    # reopen — this is the part that proves it's actually disk-backed
    pager = Pager(DB_PATH)
    for i, (pgno, slot) in enumerate(pointers):
        id_, name = decode(pager.read_record(pgno, slot))
        assert id_ == i and name == f"user{i}", f"MISMATCH at {i}: got {id_}, {name}"
    pager.close()
    print("PASSED: 1000 records survived close/reopen")

if __name__ == "__main__":
    run_test()