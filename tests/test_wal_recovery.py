"""
test_wal_recovery.py — Layer 4 verification

CRASH SIMULATION TEST:
1. Start engine. (We don't have txn_mgr yet, so we'll simulate txn_mgr manually).
2. Write 50 pages. log_write them. log_commit them.
3. Simulate crash: DO NOT call apply_to_db. So pager is empty/unwritten!
4. Close pager/wal.
5. Reopen pager/wal. Call wal.recover(pager).
6. Assert all 50 pages are readable from pager.
"""

import os
import sys
import unittest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pinedb.pager import Pager, PAGE_SIZE
from pinedb.wal import WAL

class TestWALRecovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_wal.db")

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except:
            pass

    def test_wal_crash_recovery(self):
        pager = Pager(self.db_path)
        wal = WAL(self.db_path, pager)
        
        # Allocate 50 pages
        pgnos = []
        payloads = {}
        for i in range(50):
            pgno = pager.allocate_page() # allocates empty pages
            pgnos.append(pgno)
            
            # Dirty data
            payload = bytes([i % 256]) * PAGE_SIZE
            payloads[pgno] = payload
            
        # Write to WAL (txn 1)
        txn_id = 1
        for pgno in pgnos:
            wal.log_write(txn_id, pgno, payloads[pgno])
            
        wal.log_commit(txn_id)
        
        # We DO NOT apply to pager! (Simulate crash)
        # Verify pager doesn't have the data
        for pgno in pgnos:
            data = pager.get_page(pgno)
            self.assertEqual(data, b'\x00' * PAGE_SIZE)
            
        pager.close()
        wal.close()
        
        # --- RESTART ---
        pager2 = Pager(self.db_path)
        wal2 = WAL(self.db_path, pager2)
        
        recovered = wal2.recover(pager2)
        self.assertEqual(recovered, 50, "Should have recovered 50 pages")
        
        # Verify pager has the data now
        for pgno in pgnos:
            data = pager2.get_page(pgno)
            self.assertEqual(data, payloads[pgno], f"Page {pgno} data mismatch")
            
        pager2.close()
        wal2.close()

if __name__ == "__main__":
    unittest.main()
