"""
pager.py — Layer 1: Disk I/O and page management.

Every component in PineDB reads and writes through the Pager. Nothing touches
the database file directly. Pages are fixed at 4096 bytes (matching the OS page
size) and are addressed by their integer page number (pgno).

File layout:
  Page 0   — file header (magic, version, page_count, root_pgno, free_list_head)
  Page 1   — catalog page (reserved for executor's table catalog)
  Page 2+  — B+Tree nodes and data pages

Portability note:
  os.pread/os.pwrite are POSIX-only and not available on Windows.
  We use a threading.Lock + os.lseek + os.read / os.write instead, which gives
  the same semantics (positional, unbuffered) while remaining cross-platform.
  os.fsync() works on Windows (maps to FlushFileBuffers).
"""

import os
import struct
import threading

# ─── Constants ────────────────────────────────────────────────────────────────

PAGE_SIZE: int = 4096

# File header (page 0) layout — packed into first 18 bytes, rest is zeros.
#   magic:          4s  — b'PINE'
#   version:        H   — unsigned short, always 1
#   page_count:     I   — total pages in file (including page 0)
#   root_pgno:      I   — B+Tree root page number (0 = empty tree)
#   free_list_head: I   — first free page in free-list (0 = no free pages)
FILE_HEADER_FMT = ">4sHIII"
FILE_HEADER_SIZE = struct.calcsize(FILE_HEADER_FMT)  # 18 bytes

MAGIC = b"PINE"
VERSION = 1

# Data page header (first 16 bytes of every non-header page).
#   page_type:  B   — 1 byte   (0=FREE, 1=LEAF, 2=INTERNAL, 3=DATA)
#   _pad:       3x  — 3 bytes padding for alignment
#   page_id:    I   — 4 bytes  unsigned int
#   num_slots:  H   — 2 bytes  unsigned short
#   reserved:   H   — 2 bytes  (unused, always 0)
#   next_page:  I   — 4 bytes  unsigned int (sibling / overflow pointer, 0 = none)
# Total: 1 + 3 + 4 + 2 + 2 + 4 = 16 bytes
PAGE_HEADER_FMT = ">B3xIHHI"
PAGE_HEADER_SIZE = struct.calcsize(PAGE_HEADER_FMT)  # 16 bytes

# Page type constants
PAGE_FREE = 0
PAGE_LEAF = 1
PAGE_INTERNAL = 2
PAGE_DATA = 3


# ─── Pager ────────────────────────────────────────────────────────────────────

class Pager:
    """
    Manages fixed-size page I/O against a single database file.

    All reads and writes go through get_page() and write_page() which use
    positional, unbuffered I/O (os.lseek + os.read / os.write).

    A threading.Lock serialises all seek+read and seek+write sequences so that
    the Pager is safe to use from a single thread without torn reads.

    The file header lives on page 0. It tracks:
      - total page count (used to extend the file on allocate_page)
      - the B+Tree root page number (so the tree survives restarts)
      - the free-list head (reserved for future use in V1)
    """

    def __init__(self, path: str) -> None:
        """
        Open or create the database file at *path*.

        If the file is new (empty), writes the initial file header on page 0.
        If the file exists, reads and validates the header (checks magic bytes).
        """
        self._path = path
        self._lock = threading.Lock()

        # os.O_BINARY is required on Windows to avoid CR/LF translation.
        flags = os.O_RDWR | os.O_CREAT
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY
        self._fd = os.open(path, flags, 0o666)

        file_size = os.path.getsize(path)
        if file_size == 0:
            # Brand new file: write the initial header on page 0.
            self._page_count = 1
            self._root_pgno = 0
            self._free_list_head = 0
            self._flush_header()
        else:
            # Existing file: read and validate the header.
            raw = self._pread(0)
            if len(raw) < FILE_HEADER_SIZE:
                raise RuntimeError(
                    f"Corrupt database file: header too short ({len(raw)} bytes)"
                )
            magic, version, page_count, root_pgno, free_list_head = struct.unpack_from(
                FILE_HEADER_FMT, raw, 0
            )
            if magic != MAGIC:
                raise RuntimeError(f"Not a PineDB file: bad magic {magic!r}")
            self._page_count = page_count
            self._root_pgno = root_pgno
            self._free_list_head = free_list_head

    # ── Low-level positional I/O (cross-platform) ──────────────────────────────

    def _pread(self, pgno: int) -> bytes:
        """Read PAGE_SIZE bytes at offset pgno * PAGE_SIZE (seek + read, locked)."""
        offset = pgno * PAGE_SIZE
        with self._lock:
            os.lseek(self._fd, offset, os.SEEK_SET)
            data = os.read(self._fd, PAGE_SIZE)
        return data

    def _pwrite(self, pgno: int, data: bytes) -> None:
        """Write *data* at offset pgno * PAGE_SIZE (seek + write, locked)."""
        assert len(data) == PAGE_SIZE, f"BUG: write is {len(data)} bytes, expected {PAGE_SIZE}"
        offset = pgno * PAGE_SIZE
        with self._lock:
            os.lseek(self._fd, offset, os.SEEK_SET)
            os.write(self._fd, data)

    # ── Public API ─────────────────────────────────────────────────────────────

    def get_page(self, pgno: int) -> bytes:
        """
        Read and return the 4096-byte page at page number *pgno*.

        Raises ValueError if pgno is out of range.
        Always returns exactly PAGE_SIZE bytes (zero-pads short reads).
        """
        if pgno < 0 or pgno >= self._page_count:
            raise ValueError(
                f"Page {pgno} out of range (page_count={self._page_count})"
            )
        data = self._pread(pgno)
        if len(data) < PAGE_SIZE:
            data = data + b"\x00" * (PAGE_SIZE - len(data))
        return data

    def write_page(self, pgno: int, data: bytes) -> None:
        """
        Write exactly 4096 bytes to page number *pgno*.

        Pads *data* with zeros if shorter than PAGE_SIZE. Does NOT fsync —
        callers that need durability (WAL) call os.fsync(pager._fd) themselves.
        """
        if len(data) > PAGE_SIZE:
            raise ValueError(f"Page data too large: {len(data)} > {PAGE_SIZE}")
        if len(data) < PAGE_SIZE:
            data = data + b"\x00" * (PAGE_SIZE - len(data))
        self._pwrite(pgno, data)

    def allocate_page(self) -> int:
        """
        Allocate a new page at the end of the file.

        Writes a zeroed page to extend the file, increments page_count in the
        header, and returns the new page number.
        """
        new_pgno = self._page_count
        # Write a zero page to extend the file.
        self._pwrite(new_pgno, b"\x00" * PAGE_SIZE)
        self._page_count += 1
        self._flush_header()
        return new_pgno

    def get_root_pgno(self) -> int:
        """Return the B+Tree root page number stored in the file header."""
        return self._root_pgno

    def set_root_pgno(self, pgno: int) -> None:
        """Update the B+Tree root page number in the file header."""
        self._root_pgno = pgno
        self._flush_header()

    def page_count(self) -> int:
        """Return the total number of pages currently in the file."""
        return self._page_count

    def fsync(self) -> None:
        """Force all pending writes to physical storage."""
        os.fsync(self._fd)

    def close(self) -> None:
        """Close the underlying file descriptor."""
        os.close(self._fd)

    # ── Internal helpers ───────────────────────────────────────────────────────

    def _flush_header(self) -> None:
        """
        Pack and write the file header to page 0.

        Called whenever page_count, root_pgno, or free_list_head changes.
        The header occupies the first FILE_HEADER_SIZE bytes of page 0;
        the rest is padded with zeros to PAGE_SIZE.
        """
        header = struct.pack(
            FILE_HEADER_FMT,
            MAGIC,
            VERSION,
            self._page_count,
            self._root_pgno,
            self._free_list_head,
        )
        page = header + b"\x00" * (PAGE_SIZE - len(header))
        assert len(page) == PAGE_SIZE
        self._pwrite(0, page)


# ─── Page header helpers ──────────────────────────────────────────────────────

def encode_page_header(
    page_type: int,
    page_id: int,
    num_slots: int,
    next_page: int = 0,
) -> bytes:
    """
    Pack a 16-byte data page header.

    Format '>B3xIHHI':
      page_type  (1 byte)  — PAGE_FREE / PAGE_LEAF / PAGE_INTERNAL / PAGE_DATA
      _pad       (3 bytes) — alignment padding, always zero
      page_id    (4 bytes) — this page's page number
      num_slots  (2 bytes) — number of occupied slots (records or keys)
      reserved   (2 bytes) — unused, always 0
      next_page  (4 bytes) — sibling pointer (leaf chain) or 0 if none
    """
    return struct.pack(PAGE_HEADER_FMT, page_type, page_id, num_slots, 0, next_page)


def decode_page_header(data: bytes) -> tuple:
    """
    Unpack a 16-byte data page header.

    Returns (page_type, page_id, num_slots, reserved, next_page).
    """
    return struct.unpack_from(PAGE_HEADER_FMT, data, 0)
