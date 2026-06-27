import os
import unittest
import tempfile
from pinedb.pager import Pager
from pinedb.wal import WAL
from pinedb.txn import TransactionManager
from pinedb.btree import BPlusTree

class TestWalRecovery(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "wal_test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_wal_recovery(self):
        # 1. Open engine. Begin txn. Insert 50 rows via btree directly. Commit (WAL + log_commit).
        pager = Pager(self.db_path)
        wal = WAL(self.db_path, pager)
        txn_mgr = TransactionManager(pager, wal)

        class TxnPagerProxy:
            def __init__(self, pager, txn_mgr, txn_id):
                self.pager = pager
                self.txn_mgr = txn_mgr
                self.txn_id = txn_id
            def get_page(self, pgno):
                return self.txn_mgr.read_page(self.txn_id, pgno)
            def write_page(self, pgno, data):
                self.txn_mgr.write_page(self.txn_id, pgno, data)
            def allocate_page(self):
                pgno = self.pager.allocate_page()
                self.txn_mgr.write_page(self.txn_id, pgno, b'\x00'*4096)
                return pgno
            def get_root_pgno(self):
                return self.pager.get_root_pgno()
            def set_root_pgno(self, pgno):
                self.pager.set_root_pgno(pgno)

        txn_id = txn_mgr.begin()
        tree = BPlusTree(TxnPagerProxy(pager, txn_mgr, txn_id))

        keys = list(range(1, 51))
        for k in keys:
            tree.insert(k, k*100, k%10)

        # Do NOT call apply_to_db — simulate crash before data.db is written
        buf = txn_mgr._buffers.get(txn_id, {})
        for page_id, data in buf.items():
            wal.log_write(txn_id, page_id, data)
        wal.log_commit(txn_id)

        # 2. Close pager and WAL WITHOUT flushing data.db.
        pager.close()
        wal.close()

        # 3. Reopen pager and WAL. Call wal.recover(pager).
        pager2 = Pager(self.db_path)
        wal2 = WAL(self.db_path, pager2)
        recovered = wal2.recover(pager2)
        self.assertGreater(recovered, 0)

        # 4. Read all 50 keys via btree.search(). All must return non-None.
        tree2 = BPlusTree(pager2)
        for k in keys:
            result = tree2.search(k)
            self.assertIsNotNone(result)

        pager2.close()
        wal2.close()

if __name__ == '__main__':
    unittest.main()
