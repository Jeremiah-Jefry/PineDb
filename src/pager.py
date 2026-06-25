import os
from src.record import RECORD_SIZE

PAGE_SIZE = 4096
PAGE_HEADER_SIZE = 8
RECORDS_PER_PAGE = (PAGE_SIZE - PAGE_HEADER_SIZE) // RECORD_SIZE

class Pager:
    def __init__(self, path):
        exists = os.path.exists(path)
        self.f = open(path, "r+b" if exists else "w+b")
        self.num_pages = os.path.getsize(path) // PAGE_SIZE if exists else 0

    def allocate_page(self):
        pgno = self.num_pages
        self.f.seek(pgno * PAGE_SIZE)
        self.f.write(b'\x00' * PAGE_SIZE)
        self.f.flush()
        self.num_pages += 1
        return pgno

    def read_page(self, pgno):
        self.f.seek(pgno * PAGE_SIZE)
        return bytearray(self.f.read(PAGE_SIZE))

    def write_page(self, pgno, data: bytearray):
        assert len(data) == PAGE_SIZE
        self.f.seek(pgno * PAGE_SIZE)
        self.f.write(data)
        self.f.flush()
        os.fsync(self.f.fileno())

    def close(self):
        self.f.close()

    def insert_record(self, record_bytes):
        # naive version: always use the last page, allocate new one if full
        if self.num_pages == 0:
            pgno = self.allocate_page()
        else:
            pgno = self.num_pages - 1

        page = self.read_page(pgno)
        num_records = int.from_bytes(page[0:2], "little")

        if num_records >= RECORDS_PER_PAGE:
            pgno = self.allocate_page()
            page = self.read_page(pgno)
            num_records = 0

        offset = PAGE_HEADER_SIZE + num_records * RECORD_SIZE
        page[offset:offset + RECORD_SIZE] = record_bytes
        page[0:2] = (num_records + 1).to_bytes(2, "little")
        self.write_page(pgno, page)
        return pgno, num_records  # this (pgno, slot) pair is your record pointer

    def read_record(self, pgno, slot):
        page = self.read_page(pgno)
        offset = PAGE_HEADER_SIZE + slot * RECORD_SIZE
        return page[offset:offset + RECORD_SIZE]