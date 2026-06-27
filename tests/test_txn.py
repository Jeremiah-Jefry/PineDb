import os
import unittest
import tempfile
from pinedb.pager import Pager
from pinedb.wal import WAL
from pinedb.txn import TransactionManager
from pinedb.executor import Executor
from pinedb.parser import Parser

class TestTxn(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "txn_test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_txn_commit_and_discard(self):
        pager = Pager(self.db_path)
        wal = WAL(self.db_path, pager)
        txn_mgr = TransactionManager(pager, wal)
        executor = Executor(pager, wal, txn_mgr)

        executor.execute(Parser("CREATE TABLE t (id INT, name VARCHAR);").parse())

        # 1. Begin txn. Insert 3 rows via executor. Commit.
        executor.execute(Parser("BEGIN;").parse())
        executor.execute(Parser("INSERT INTO t VALUES (1, 'alice');").parse())
        executor.execute(Parser("INSERT INTO t VALUES (2, 'bob');").parse())
        executor.execute(Parser("INSERT INTO t VALUES (3, 'charlie');").parse())
        executor.execute(Parser("COMMIT;").parse())

        res1 = executor.execute(Parser("SELECT * FROM t;").parse())
        self.assertEqual(len(res1), 3)

        # 2. Begin txn. Insert 3 more rows. Do NOT commit. Discard txn buffer.
        txn_id2 = txn_mgr.begin()
        executor.execute(Parser("INSERT INTO t VALUES (4, 'dave');").parse())
        executor.execute(Parser("INSERT INTO t VALUES (5, 'eve');").parse())
        executor.execute(Parser("INSERT INTO t VALUES (6, 'frank');").parse())
        # discard buffer directly to simulate rollback/abort
        del txn_mgr._buffers[txn_id2]

        pager.close()
        wal.close()

        # 3. Reopen engine. Verify original 3 rows still present. New 3 absent.
        pager2 = Pager(self.db_path)
        wal2 = WAL(self.db_path, pager2)
        txn_mgr2 = TransactionManager(pager2, wal2)
        executor2 = Executor(pager2, wal2, txn_mgr2)

        res2 = executor2.execute(Parser("SELECT * FROM t;").parse())
        self.assertEqual(len(res2), 3)

        ids = set(row['id'] for row in res2)
        self.assertEqual(ids, {1, 2, 3})

        pager2.close()
        wal2.close()

if __name__ == '__main__':
    unittest.main()
