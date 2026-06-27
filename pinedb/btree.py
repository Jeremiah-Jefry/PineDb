import struct
from pinedb.pager import Pager, PAGE_SIZE, PAGE_LEAF, PAGE_INTERNAL

KEY_SIZE   = 4
VAL_SIZE   = 6
ENTRY_SIZE = 10
PTR_SIZE   = 4

PAGE_HEADER_SIZE = 16
PAGE_BODY_SIZE = PAGE_SIZE - PAGE_HEADER_SIZE

LEAF_ORDER    = PAGE_BODY_SIZE // ENTRY_SIZE
INTERNAL_ORDER = (PAGE_BODY_SIZE - PTR_SIZE) // (KEY_SIZE + PTR_SIZE)

class BPlusTree:
    def __init__(self, pager: Pager, root_pgno: int = 0):
        self.pager = pager
        self.root_pgno = root_pgno
        if self.root_pgno == 0:
            self.root_pgno = self.pager.get_root_pgno()

    def search(self, key: int) -> tuple[int, int] | None:
        if self.root_pgno == 0:
            return None

        curr_pgno = self.root_pgno
        while True:
            page_data = self.pager.get_page(curr_pgno)
            page_type = struct.unpack('>B', page_data[0:1])[0]

            if page_type == PAGE_LEAF:
                break
            elif page_type == PAGE_INTERNAL:
                _, keys, children = self._read_internal(curr_pgno)
                found = False
                for i, k in enumerate(keys):
                    if key < k:
                        curr_pgno = children[i]
                        found = True
                        break
                if not found:
                    curr_pgno = children[-1]
            else:
                raise ValueError(f"Invalid page type {page_type} in BTree")

        _, entries, _ = self._read_leaf(curr_pgno)
        # Binary search
        left, right = 0, len(entries) - 1
        while left <= right:
            mid = (left + right) // 2
            k, pg, slot = entries[mid]
            if k == key:
                return (pg, slot)
            elif k < key:
                left = mid + 1
            else:
                right = mid - 1
        return None

    def insert(self, key: int, record_page: int, record_slot: int) -> None:
        if self.root_pgno == 0:
            new_root_pgno = self.pager.allocate_page()
            self._write_leaf(new_root_pgno, [(key, record_page, record_slot)], 0)
            self.root_pgno = new_root_pgno
            self.pager.set_root_pgno(self.root_pgno)
            return

        leaf_pgno, parent_path = self._find_leaf(key)
        _, entries, next_page = self._read_leaf(leaf_pgno)

        # Insert entry in sorted order
        inserted = False
        for i, (k, _, _) in enumerate(entries):
            if key < k:
                entries.insert(i, (key, record_page, record_slot))
                inserted = True
                break
            elif key == k:
                # Update existing (if we want to support it, but assuming unique for now)
                entries[i] = (key, record_page, record_slot)
                inserted = True
                break

        if not inserted:
            entries.append((key, record_page, record_slot))

        if len(entries) <= LEAF_ORDER:
            self._write_leaf(leaf_pgno, entries, next_page)
        else:
            new_leaf_pgno, push_key = self._split_leaf(leaf_pgno, entries)
            self._insert_into_parent(leaf_pgno, push_key, new_leaf_pgno, parent_path)

    def _read_leaf(self, pgno: int) -> tuple[int, list[tuple[int,int,int]], int]:
        data = self.pager.get_page(pgno)
        page_type, page_id, num_slots, _, next_page = struct.unpack('>B3xIHHI', data[:16])
        if page_type != PAGE_LEAF:
            raise ValueError(f"Page {pgno} is not a leaf node (type {page_type})")

        entries = []
        offset = 16
        for _ in range(num_slots):
            k, p, s = struct.unpack('>iIH', data[offset:offset+10])
            entries.append((k, p, s))
            offset += 10

        return page_id, entries, next_page

    def _write_leaf(self, pgno: int, entries: list[tuple[int,int,int]], next_page: int) -> None:
        num_slots = len(entries)
        header = struct.pack('>B3xIHHI', PAGE_LEAF, pgno, num_slots, 0, next_page)

        body = bytearray()
        for k, p, s in entries:
            body.extend(struct.pack('>iIH', k, p, s))

        page_data = header + body
        page_data = page_data.ljust(PAGE_SIZE, b'\x00')
        assert len(page_data) == PAGE_SIZE
        self.pager.write_page(pgno, page_data)

    def _read_internal(self, pgno: int) -> tuple[int, list[int], list[int]]:
        data = self.pager.get_page(pgno)
        page_type, page_id, num_slots, _, _ = struct.unpack('>B3xIHHI', data[:16])
        if page_type != PAGE_INTERNAL:
            raise ValueError(f"Page {pgno} is not an internal node (type {page_type})")

        keys = []
        children = []
        offset = 16

        # Read first child pointer
        children.append(struct.unpack('>I', data[offset:offset+4])[0])
        offset += 4

        for _ in range(num_slots):
            k = struct.unpack('>i', data[offset:offset+4])[0]
            keys.append(k)
            offset += 4

            c = struct.unpack('>I', data[offset:offset+4])[0]
            children.append(c)
            offset += 4

        return page_id, keys, children

    def _write_internal(self, pgno: int, keys: list[int], children: list[int]) -> None:
        num_slots = len(keys)
        header = struct.pack('>B3xIHHI', PAGE_INTERNAL, pgno, num_slots, 0, 0)

        body = bytearray()
        body.extend(struct.pack('>I', children[0]))

        for i in range(num_slots):
            body.extend(struct.pack('>i', keys[i]))
            body.extend(struct.pack('>I', children[i+1]))

        page_data = header + body
        page_data = page_data.ljust(PAGE_SIZE, b'\x00')
        assert len(page_data) == PAGE_SIZE
        self.pager.write_page(pgno, page_data)

    def _find_leaf(self, key: int) -> tuple[int, list[tuple[int, int]]]:
        curr_pgno = self.root_pgno
        parent_path = []

        while True:
            data = self.pager.get_page(curr_pgno)
            page_type = struct.unpack('>B', data[0:1])[0]

            if page_type == PAGE_LEAF:
                return curr_pgno, parent_path
            elif page_type == PAGE_INTERNAL:
                _, keys, children = self._read_internal(curr_pgno)

                found = False
                for i, k in enumerate(keys):
                    if key < k:
                        parent_path.append((curr_pgno, i))
                        curr_pgno = children[i]
                        found = True
                        break

                if not found:
                    parent_path.append((curr_pgno, len(keys)))
                    curr_pgno = children[-1]
            else:
                raise ValueError("Invalid page type during traversal")

    def _split_leaf(self, pgno: int, entries: list) -> tuple[int, int]:
        _, _, old_next_page = self._read_leaf(pgno)
        mid = len(entries) // 2

        left_entries = entries[:mid]
        right_entries = entries[mid:]

        new_leaf_pgno = self.pager.allocate_page()

        self._write_leaf(new_leaf_pgno, right_entries, old_next_page)
        self._write_leaf(pgno, left_entries, new_leaf_pgno)

        return new_leaf_pgno, right_entries[0][0]

    def _split_internal(self, pgno: int, keys: list, children: list) -> tuple[int, int]:
        mid = len(keys) // 2

        left_keys = keys[:mid]
        left_children = children[:mid+1]

        median_key = keys[mid]

        right_keys = keys[mid+1:]
        right_children = children[mid+1:]

        new_internal_pgno = self.pager.allocate_page()

        self._write_internal(new_internal_pgno, right_keys, right_children)
        self._write_internal(pgno, left_keys, left_children)

        return new_internal_pgno, median_key

    def _insert_into_parent(self, left_pgno: int, push_key: int, right_pgno: int, parent_path: list[tuple[int, int]]) -> None:
        if not parent_path:
            new_root_pgno = self.pager.allocate_page()
            self._write_internal(new_root_pgno, [push_key], [left_pgno, right_pgno])
            self.root_pgno = new_root_pgno
            self.pager.set_root_pgno(self.root_pgno)
            return

        parent_pgno, insert_index = parent_path.pop()
        _, keys, children = self._read_internal(parent_pgno)

        keys.insert(insert_index, push_key)
        children.insert(insert_index + 1, right_pgno)

        if len(keys) <= INTERNAL_ORDER:
            self._write_internal(parent_pgno, keys, children)
        else:
            new_internal_pgno, median_key = self._split_internal(parent_pgno, keys, children)
            self._insert_into_parent(parent_pgno, median_key, new_internal_pgno, parent_path)
