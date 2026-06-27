from pinedb.pager import Pager, PAGE_SIZE
from pinedb.wal import WAL

class TransactionManager:
    def __init__(self, pager: Pager, wal: WAL):
        self.pager = pager
        self.wal = wal
        self._next_txn_id = 1
        self._buffers: dict[int, dict[int, bytes]] = {}

    def begin(self) -> int:
        txn_id = self._next_txn_id
        self._next_txn_id += 1
        self._buffers[txn_id] = {}
        return txn_id

    def read_page(self, txn_id: int, page_id: int) -> bytes:
        if txn_id in self._buffers and page_id in self._buffers[txn_id]:
            return self._buffers[txn_id][page_id]
        return self.pager.get_page(page_id)

    def write_page(self, txn_id: int, page_id: int, data: bytes) -> None:
        assert len(data) == PAGE_SIZE
        self._buffers[txn_id][page_id] = data

    def commit(self, txn_id: int) -> None:
        buf = self._buffers.get(txn_id, {})
        for page_id, data in buf.items():
            self.wal.log_write(txn_id, page_id, data)
        self.wal.log_commit(txn_id)
        self.wal.apply_to_db(txn_id)
        if txn_id in self._buffers:
            del self._buffers[txn_id]

    def active_txns(self) -> list[int]:
        return list(self._buffers.keys())
