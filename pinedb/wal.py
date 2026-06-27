import os
import struct
import zlib
from pinedb.pager import Pager, PAGE_SIZE

WAL_RECORD_SIZE = 4109

REC_PAGE_WRITE = 1
REC_COMMIT     = 2
REC_CHECKPOINT = 3

class WAL:
    def __init__(self, db_path: str, pager: Pager):
        self.pager = pager
        if db_path.endswith('.db'):
            self.wal_path = db_path.replace('.db', '.wal')
        else:
            self.wal_path = db_path + '.wal'

        self.wal_fd = os.open(self.wal_path, os.O_RDWR | os.O_CREAT | os.O_APPEND, 0o644)
        self._txn_buffers: dict[int, list[tuple[int, bytes]]] = {}

    def log_write(self, txn_id: int, page_id: int, page_data: bytes) -> None:
        assert len(page_data) == PAGE_SIZE
        checksum = zlib.crc32(page_data) & 0xFFFFFFFF
        header = struct.pack('>IIBI', txn_id, page_id, REC_PAGE_WRITE, checksum)
        record = header + page_data
        assert len(record) == WAL_RECORD_SIZE

        os.write(self.wal_fd, record)
        os.fsync(self.wal_fd)

        if txn_id not in self._txn_buffers:
            self._txn_buffers[txn_id] = []
        self._txn_buffers[txn_id].append((page_id, page_data))

    def log_commit(self, txn_id: int) -> None:
        page_data = b'\x00' * PAGE_SIZE
        checksum = 0
        header = struct.pack('>IIBI', txn_id, 0, REC_COMMIT, checksum)
        record = header + page_data
        assert len(record) == WAL_RECORD_SIZE

        os.write(self.wal_fd, record)
        os.fsync(self.wal_fd)

    def apply_to_db(self, txn_id: int) -> None:
        if txn_id in self._txn_buffers:
            for page_id, page_data in self._txn_buffers[txn_id]:
                self.pager.write_page(page_id, page_data)
            os.fsync(self.pager.fd)
            del self._txn_buffers[txn_id]

    def recover(self, pager: Pager) -> int:
        fd = os.open(self.wal_path, os.O_RDONLY)
        pending: dict[int, list[tuple[int, bytes]]] = {}
        committed: set[int] = set()

        while True:
            record = os.pread(fd, WAL_RECORD_SIZE, os.lseek(fd, 0, os.SEEK_CUR))
            if not record:
                break
            if len(record) < WAL_RECORD_SIZE:
                break # truncated record

            os.lseek(fd, WAL_RECORD_SIZE, os.SEEK_CUR)

            header = record[:13]
            page_data = record[13:]
            txn_id, page_id, rec_type, checksum = struct.unpack('>IIBI', header)

            if rec_type == REC_PAGE_WRITE:
                actual_crc = zlib.crc32(page_data) & 0xFFFFFFFF
                if actual_crc != checksum:
                    import sys
                    print("Warning: WAL corruption detected.", file=sys.stderr)
                    break
                if txn_id not in pending:
                    pending[txn_id] = []
                pending[txn_id].append((page_id, page_data))

            elif rec_type == REC_COMMIT:
                committed.add(txn_id)

            elif rec_type == REC_CHECKPOINT:
                pending.clear()
                committed.clear()

        os.close(fd)

        pages_written = 0
        for txn_id in committed:
            if txn_id in pending:
                for page_id, page_data in pending[txn_id]:
                    pager.write_page(page_id, page_data)
                    pages_written += 1

        os.fsync(pager.fd)
        os.ftruncate(self.wal_fd, 0)

        return pages_written

    def close(self) -> None:
        os.close(self.wal_fd)
