import os
import random
import unittest
import tempfile
from pinedb.pager import Pager
from pinedb.btree import BPlusTree

class TestBTree(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "btree_test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_btree_insert_search(self):
        pager = Pager(self.db_path)
        tree = BPlusTree(pager)

        keys = list(range(1, 5001))
        random.shuffle(keys)

        for k in keys:
            # use a dummy record page and slot
            tree.insert(k, k * 10, k % 100)

        pager.close()

        # Reopen
        pager2 = Pager(self.db_path)
        tree2 = BPlusTree(pager2)

        for k in keys:
            result = tree2.search(k)
            self.assertIsNotNone(result)
            page_no, slot = result
            self.assertEqual(page_no, k * 10)
            self.assertEqual(slot, k % 100)

        # Search for missing keys
        for i in range(5001, 5101):
            result = tree2.search(i)
            self.assertIsNone(result)

        pager2.close()

if __name__ == '__main__':
    unittest.main()
