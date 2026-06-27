"""
test_txn.py — Layer 5 verification

1. Begin txn. Insert rows (pages) A, B, C. Commit. Verify A, B, C readable.
2. Begin txn. Insert pages X, Y. DO NOT commit. "Crash" (discard buffer).
3. Reopen. Verify X, Y do NOT exist. A, B, C still exist.
"""

import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pinedb.pager import Pager, PAGE_SIZE
from pinedb.wal import WAL
from pinedb.txn import TransactionManager

class TestTransactionManager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_txn.db")

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except:
            pass

    def test_txn_commit_and_crash(self):
        pager = Pager(self.db_path)
        wal = WAL(self.db_path, pager)
        txn_mgr = TransactionManager(pager, wal)
        
        # 1. Commit A, B, C
        txn1 = txn_mgr.begin()
        pgA = pager.allocate_page()
        pgB = pager.allocate_page()
        pgC = pager.allocate_page()
        
        dataA = b'A' * PAGE_SIZE
        dataB = b'B' * PAGE_SIZE
        dataC = b'C' * PAGE_SIZE
        
        txn_mgr.write_page(txn1, pgA, dataA)
        txn_mgr.write_page(txn1, pgB, dataB)
        txn_mgr.write_page(txn1, pgC, dataC)
        
        # Before commit, pager should not have them
        self.assertEqual(pager.get_page(pgA), b'\x00' * PAGE_SIZE)
        
        txn_mgr.commit(txn1)
        
        # After commit, pager has them
        self.assertEqual(pager.get_page(pgA), dataA)
        
        # 2. Uncommitted X, Y
        txn2 = txn_mgr.begin()
        pgX = pager.allocate_page()
        pgY = pager.allocate_page()
        
        dataX = b'X' * PAGE_SIZE
        dataY = b'Y' * PAGE_SIZE
        
        txn_mgr.write_page(txn2, pgX, dataX)
        txn_mgr.write_page(txn2, pgY, dataY)
        
        # Crash! (Close without commit)
        pager.close()
        wal.close()
        
        # 3. Reopen
        pager2 = Pager(self.db_path)
        wal2 = WAL(self.db_path, pager2)
        txn_mgr2 = TransactionManager(pager2, wal2)
        
        wal2.recover(pager2)
        
        # Verify A, B, C exist
        self.assertEqual(pager2.get_page(pgA), dataA)
        self.assertEqual(pager2.get_page(pgB), dataB)
        self.assertEqual(pager2.get_page(pgC), dataC)
        
        # Verify X, Y do NOT exist
        self.assertEqual(pager2.get_page(pgX), b'\x00' * PAGE_SIZE)
        self.assertEqual(pager2.get_page(pgY), b'\x00' * PAGE_SIZE)
        
        pager2.close()
        wal2.close()

if __name__ == "__main__":
    unittest.main()
