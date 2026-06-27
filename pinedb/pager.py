import os
import struct

PAGE_SIZE = 4096
MAGIC = b'PINE'

PAGE_FREE     = 0
PAGE_LEAF     = 1
PAGE_INTERNAL = 2
PAGE_DATA     = 3
PAGE_HEADER   = 4

class Pager:
    def __init__(self, path: str):
        self.path = path
        is_new = not os.path.exists(path) or os.path.getsize(path) == 0
        self.fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o644)

        self._page_count = 0
        self._root_pgno = 0
        self._free_list_head = 0

        if is_new:
            self._page_count = 1
            self._root_pgno = 0
            self._free_list_head = 0
            self.flush_header()
        else:
            header_data = os.pread(self.fd, PAGE_SIZE, 0)
            if len(header_data) < 18:
                raise ValueError("Invalid file header")
            magic, version, page_count, root_pgno, free_list_head = struct.unpack('>4sHIII', header_data[:18])
            if magic != MAGIC:
                raise ValueError("Invalid database file")
            self._page_count = page_count
            self._root_pgno = root_pgno
            self._free_list_head = free_list_head

    def get_page(self, pgno: int) -> bytes:
        if pgno >= self._page_count:
            raise ValueError(f"Page number {pgno} out of bounds")
        data = os.pread(self.fd, PAGE_SIZE, pgno * PAGE_SIZE)
        if len(data) < PAGE_SIZE:
            data = data.ljust(PAGE_SIZE, b'\x00')
        return data

    def write_page(self, pgno: int, data: bytes) -> None:
        if pgno >= self._page_count:
            raise ValueError(f"Page number {pgno} out of bounds")
        if len(data) < PAGE_SIZE:
            data = data.ljust(PAGE_SIZE, b'\x00')
        assert len(data) == PAGE_SIZE
        os.pwrite(self.fd, data, pgno * PAGE_SIZE)

    def allocate_page(self) -> int:
        new_pgno = self._page_count
        self._page_count += 1
        self.flush_header()

        # Extend the file with a zero-filled page
        zero_page = b'\x00' * PAGE_SIZE
        os.pwrite(self.fd, zero_page, new_pgno * PAGE_SIZE)

        return new_pgno

    def get_root_pgno(self) -> int:
        return self._root_pgno

    def set_root_pgno(self, pgno: int) -> None:
        self._root_pgno = pgno
        self.flush_header()

    def page_count(self) -> int:
        return self._page_count

    def flush_header(self) -> None:
        header_data = struct.pack('>4sHIII', MAGIC, 1, self._page_count, self._root_pgno, self._free_list_head)
        header_page = header_data.ljust(PAGE_SIZE, b'\x00')
        assert len(header_page) == PAGE_SIZE
        os.pwrite(self.fd, header_page, 0)

    def close(self) -> None:
        os.close(self.fd)
