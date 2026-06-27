"""
txn.py — Layer 5: Transactions

V1: COMMIT only. No ROLLBACK.
Buffers writes in memory. On COMMIT, writes to WAL, then db.
"""

from pinedb.pager import Pager
from pinedb.wal import WAL

class TransactionManager:
    def __init__(self, pager: Pager, wal: WAL):
        self.pager = pager
        self.wal = wal
        self._next_txn_id = 1
        self._buffer = {} # txn_id -> {page_id -> data: bytes}

    def begin(self) -> int:
        txn_id = self._next_txn_id
        self._next_txn_id += 1
        self._buffer[txn_id] = {}
        return txn_id

    def write_page(self, txn_id: int, page_id: int, data: bytes) -> None:
        if txn_id not in self._buffer:
            raise ValueError(f"Invalid transaction ID: {txn_id}")
        if len(data) < 4096:
            data = data.ljust(4096, b'\x00')
        self._buffer[txn_id][page_id] = data

    def read_page(self, txn_id: int, page_id: int) -> bytes:
        if txn_id in self._buffer and page_id in self._buffer[txn_id]:
            return self._buffer[txn_id][page_id]
        return self.pager.get_page(page_id)

    def commit(self, txn_id: int) -> None:
        if txn_id not in self._buffer:
            raise ValueError(f"Invalid transaction ID: {txn_id}")
            
        dirty_pages = self._buffer[txn_id]
        
        # 1. Write WAL records
        for page_id, data in dirty_pages.items():
            self.wal.log_write(txn_id, page_id, data)
            
        # 2. Write COMMIT record
        self.wal.log_commit(txn_id)
        
        # 3. Apply to data.db
        self.wal.apply_to_db(txn_id, dirty_pages)
        
        del self._buffer[txn_id]
