"""
test_btree.py — Layer 3 verification

1. Insert 5000 random unique integers
2. Close and reopen pager
3. Search for all 5000 keys — all must be found
4. Search for 100 keys NOT inserted — all must return None
"""

import os
import sys
import random
import unittest
import tempfile

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pinedb.pager import Pager
from pinedb.btree import BPlusTree

class TestBTree(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_btree.db")

    def tearDown(self):
        try:
            self.temp_dir.cleanup()
        except:
            pass

    def test_btree(self):
        pager = Pager(self.db_path)
        tree = BPlusTree(pager)
        
        # 1. Insert 5000 random unique integers
        random.seed(42)
        keys_to_insert = random.sample(range(-100000, 100000), 5000)
        
        for k in keys_to_insert:
            # We don't have real records yet, so use dummy (page, slot) like (abs(k), abs(k)%10)
            tree.insert(k, abs(k), abs(k) % 10)
            
        # 2. Close and reopen pager
        pager.close()
        
        pager2 = Pager(self.db_path)
        tree2 = BPlusTree(pager2)
        
        # 3. Search for all 5000 keys
        missing = 0
        for k in keys_to_insert:
            res = tree2.search(k)
            if res is None:
                missing += 1
            else:
                self.assertEqual(res, (abs(k), abs(k) % 10))
                
        self.assertEqual(missing, 0, f"Missing {missing} keys after reopen")
        
        # 4. Search for 100 keys NOT inserted
        not_inserted = [x for x in range(200000, 200100)]
        for k in not_inserted:
            self.assertIsNone(tree2.search(k))
            
        pager2.close()

if __name__ == "__main__":
    unittest.main()
