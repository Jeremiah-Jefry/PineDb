"""
wal.py — Layer 4: Write-ahead log and crash recovery

Records every page modification before it reaches data.db.
WAL record format (4109 bytes):
  txn_id    (4 bytes)
  page_id   (4 bytes)
  rec_type  (1 byte: 1=PAGE_WRITE, 2=COMMIT, 3=CHECKPOINT)
  checksum  (4 bytes: CRC32 of page_data, 0 for COMMIT)
  page_data (4096 bytes)
"""

import os
import struct
import zlib
import threading
from pinedb.pager import Pager, PAGE_SIZE

REC_PAGE_WRITE = 1
REC_COMMIT = 2
REC_CHECKPOINT = 3

WAL_REC_FMT = ">IIBI"
WAL_REC_HEADER_SIZE = struct.calcsize(WAL_REC_FMT)  # 13 bytes
WAL_REC_SIZE = WAL_REC_HEADER_SIZE + PAGE_SIZE      # 4109 bytes

class WAL:
    def __init__(self, db_path: str, pager: Pager):
        self.pager = pager
        self.wal_path = db_path + ".wal"
        self._lock = threading.Lock()
        
        flags = os.O_RDWR | os.O_CREAT | os.O_APPEND
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
            
        self.fd = os.open(self.wal_path, flags, 0o666)

    def log_write(self, txn_id: int, page_id: int, page_data: bytes) -> None:
        assert len(page_data) == PAGE_SIZE
        checksum = zlib.crc32(page_data) & 0xFFFFFFFF
        header = struct.pack(WAL_REC_FMT, txn_id, page_id, REC_PAGE_WRITE, checksum)
        
        with self._lock:
            os.write(self.fd, header + page_data)
            os.fsync(self.fd)

    def log_commit(self, txn_id: int) -> None:
        header = struct.pack(WAL_REC_FMT, txn_id, 0, REC_COMMIT, 0)
        page_data = b'\x00' * PAGE_SIZE
        
        with self._lock:
            os.write(self.fd, header + page_data)
            os.fsync(self.fd)

    def apply_to_db(self, txn_id: int, dirty_pages: dict) -> None:
        """
        After commit: write buffered page_data for this txn to actual data.db
        """
        for page_id, data in dirty_pages.items():
            self.pager.write_page(page_id, data)
        self.pager.fsync()

    def recover(self, pager: Pager) -> int:
        """
        Called on startup BEFORE anything else.
        Reads WAL from beginning.
        """
        try:
            size = os.path.getsize(self.wal_path)
            if size == 0:
                return 0
        except FileNotFoundError:
            return 0
            
        # Re-open for reading from start
        flags = os.O_RDONLY
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        read_fd = os.open(self.wal_path, flags)
        
        txns = {} # txn_id -> {page_id: data}
        committed_txns = set()
        
        try:
            while True:
                record = os.read(read_fd, WAL_REC_SIZE)
                if not record:
                    break
                if len(record) < WAL_REC_SIZE:
                    # Partial write from crash, discard remainder
                    print(f"Warning: discarding partial WAL record ({len(record)} bytes)")
                    break
                    
                txn_id, page_id, rec_type, checksum = struct.unpack_from(WAL_REC_FMT, record, 0)
                page_data = record[WAL_REC_HEADER_SIZE:]
                
                if rec_type == REC_PAGE_WRITE:
                    calc_checksum = zlib.crc32(page_data) & 0xFFFFFFFF
                    if calc_checksum != checksum:
                        print(f"Warning: bad checksum in WAL for txn {txn_id} page {page_id}, stopping recovery")
                        break
                        
                    if txn_id not in txns:
                        txns[txn_id] = {}
                    txns[txn_id][page_id] = page_data
                    
                elif rec_type == REC_COMMIT:
                    committed_txns.add(txn_id)
        finally:
            os.close(read_fd)
            
        # Apply committed txns to db
        recovered_pages = 0
        for txn_id in committed_txns:
            if txn_id in txns:
                for page_id, data in txns[txn_id].items():
                    pager.write_page(page_id, data)
                    recovered_pages += 1
                    
        pager.fsync()
        
        # Truncate WAL (reset)
        with self._lock:
            # os.ftruncate may fail on Windows if file is appended/locked differently?
            # actually os.ftruncate(fd, 0) is usually ok if opened for writing
            os.lseek(self.fd, 0, os.SEEK_SET)
            os.ftruncate(self.fd, 0)
            
        return recovered_pages

    def close(self) -> None:
        os.close(self.fd)
