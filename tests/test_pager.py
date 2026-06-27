import os
import unittest
import tempfile
from pinedb.pager import Pager, PAGE_SIZE

class TestPager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test.db")

    def tearDown(self):
        self.temp_dir.cleanup()

    def test_pager_read_write(self):
        # 1. Create pager. Allocate 100 pages.
        pager = Pager(self.db_path)
        page_contents = {}

        # page 0 is header. Let's allocate 100 pages.
        # But wait, allocating starts from page_count.
        for i in range(1, 101):
            pgno = pager.allocate_page()
            self.assertEqual(pgno, i)
            payload = bytes([i % 256]) * PAGE_SIZE
            pager.write_page(pgno, payload)
            page_contents[pgno] = payload

        # 2. Close (os.close).
        pager.close()

        # 3. Reopen same path. Read all 100 pages. Assert byte-for-byte match.
        pager2 = Pager(self.db_path)
        self.assertEqual(pager2.page_count(), 101) # header + 100 pages

        for pgno, expected_payload in page_contents.items():
            data = pager2.get_page(pgno)
            self.assertEqual(data, expected_payload)

        pager2.close()

if __name__ == '__main__':
    unittest.main()
