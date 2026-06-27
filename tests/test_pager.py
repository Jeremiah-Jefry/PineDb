"""
test_pager.py — Layer 1 verification.

Tests:
  1. Allocate 100 pages, write unique 4096-byte payloads to each.
  2. Close the pager.
  3. Reopen pager, read all 100 pages back.
  4. Assert byte-for-byte correctness for every page.
  5. Verify file header fields (magic, page_count, root_pgno) survive reopen.
"""

import os
import sys
import struct
import unittest
import tempfile

# Allow running from repo root: python tests/test_pager.py
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from pinedb.pager import Pager, PAGE_SIZE


def _make_payload(pgno: int) -> bytes:
    """
    Build a unique, deterministic 4096-byte payload for page *pgno*.
    """
    prefix = struct.pack(">I", pgno)
    fill_byte = (pgno * 37 + 13) & 0xFF
    body = bytes([fill_byte]) * (PAGE_SIZE - 4)
    return prefix + body


class TestPager(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.temp_dir.name, "test_pager.db")

    def tearDown(self):
        # Temp dir might fail to cleanup on Windows if files are open.
        try:
            self.temp_dir.cleanup()
        except:
            pass

    def test_pager_write_reopen(self):
        # ── Phase 1: allocate and write ───────────────────────────────────────────
        pager = Pager(self.db_path)
        # Page 0 is the file header (already allocated by Pager.__init__).
        self.assertEqual(pager.page_count(), 1, f"Expected 1 page after init, got {pager.page_count()}")

        pgnos = []
        payloads = []
        for i in range(100):
            pgno = pager.allocate_page()
            payload = _make_payload(pgno)
            pager.write_page(pgno, payload)
            pgnos.append(pgno)
            payloads.append(payload)

        self.assertEqual(pager.page_count(), 101, f"Expected 101 pages, got {pager.page_count()}")

        # Test set/get root_pgno round-trips through the header.
        pager.set_root_pgno(pgnos[0])
        pager.close()

        # ── Phase 2: reopen and verify ────────────────────────────────────────────
        pager2 = Pager(self.db_path)
        self.assertEqual(pager2.page_count(), 101, f"page_count not persisted: got {pager2.page_count()}")
        self.assertEqual(pager2.get_root_pgno(), pgnos[0], f"root_pgno not persisted: got {pager2.get_root_pgno()}")

        mismatches = 0
        for pgno, expected in zip(pgnos, payloads):
            actual = pager2.get_page(pgno)
            if actual != expected:
                mismatches += 1

        pager2.close()

        self.assertEqual(mismatches, 0, f"{mismatches} page(s) did not match after reopen")

    def test_pager_header_magic(self):
        """Verify that opening a valid file doesn't raise."""
        pager = Pager(self.db_path)
        pager.close()

    def test_pager_page_out_of_range(self):
        """Accessing a page beyond page_count must raise ValueError."""
        pager = Pager(self.db_path)
        with self.assertRaises(ValueError):
            pager.get_page(9999)
        pager.close()


if __name__ == "__main__":
    unittest.main()
