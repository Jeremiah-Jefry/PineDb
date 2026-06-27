"""
btree.py — Layer 3: B+Tree index

Keys are signed 32-bit integers.
Values are (page_no: uint32, slot: uint16).
Nodes are exactly 4096 bytes.
"""

import struct
from pinedb.pager import (
    Pager, PAGE_SIZE, PAGE_LEAF, PAGE_INTERNAL,
    encode_page_header, decode_page_header
)

LEAF_ORDER = 408
INTERNAL_ORDER = 509

class BPlusTree:
    def __init__(self, pager: Pager, txn_mgr=None):
        """
        txn_mgr is injected later (Layer 5) for transactional writes.
        If None, we write directly to the pager.
        """
        self.pager = pager
        self.txn_mgr = txn_mgr

    def read_page(self, pgno: int, txn_id: int = None) -> bytes:
        if self.txn_mgr and txn_id is not None:
            return self.txn_mgr.read_page(txn_id, pgno)
        return self.pager.get_page(pgno)

    def write_page(self, pgno: int, data: bytes, txn_id: int = None) -> None:
        if self.txn_mgr and txn_id is not None:
            self.txn_mgr.write_page(txn_id, pgno, data)
        else:
            self.pager.write_page(pgno, data)

    def allocate_page(self, txn_id: int = None) -> int:
        # Pager.allocate_page always extends the file directly and returns a pgno.
        # The new page is empty.
        pgno = self.pager.allocate_page()
        # Ensure the page is dirty in the transaction buffer if using txn
        if self.txn_mgr and txn_id is not None:
            self.write_page(pgno, b'\x00' * PAGE_SIZE, txn_id)
        return pgno

    def get_root_pgno(self) -> int:
        return self.pager.get_root_pgno()

    def set_root_pgno(self, pgno: int) -> None:
        self.pager.set_root_pgno(pgno)

    # --- Node parsing helpers ---

    def _parse_leaf(self, data: bytes):
        page_type, page_id, num_slots, _, next_page = decode_page_header(data)
        assert page_type == PAGE_LEAF
        entries = []
        offset = 16
        for _ in range(num_slots):
            key = struct.unpack_from('>i', data, offset)[0]
            val_pg, val_slot = struct.unpack_from('>IH', data, offset + 4)
            entries.append((key, val_pg, val_slot))
            offset += 10
        return page_id, next_page, entries

    def _pack_leaf(self, page_id: int, next_page: int, entries: list) -> bytes:
        header = encode_page_header(PAGE_LEAF, page_id, len(entries), next_page)
        body = b''.join(struct.pack('>iIH', k, p, s) for k, p, s in entries)
        return header + body

    def _parse_internal(self, data: bytes):
        page_type, page_id, num_slots, _, _ = decode_page_header(data)
        assert page_type == PAGE_INTERNAL
        children = []
        keys = []
        offset = 16
        for _ in range(num_slots):
            child = struct.unpack_from('>I', data, offset)[0]
            key = struct.unpack_from('>i', data, offset + 4)[0]
            children.append(child)
            keys.append(key)
            offset += 8
        # last child
        last_child = struct.unpack_from('>I', data, offset)[0]
        children.append(last_child)
        return page_id, keys, children

    def _pack_internal(self, page_id: int, keys: list, children: list) -> bytes:
        header = encode_page_header(PAGE_INTERNAL, page_id, len(keys), 0)
        body = b''
        for i in range(len(keys)):
            body += struct.pack('>Ii', children[i], keys[i])
        body += struct.pack('>I', children[-1])
        return header + body

    # --- Search ---

    def search(self, key: int, txn_id: int = None) -> tuple[int, int] | None:
        root_pgno = self.get_root_pgno()
        if root_pgno == 0:
            return None
        
        leaf_pgno = self._find_leaf(key, root_pgno, txn_id)
        data = self.read_page(leaf_pgno, txn_id)
        _, _, entries = self._parse_leaf(data)
        
        # Binary search could be used here, but linear is fine for in-memory lists of size < 408
        for k, val_pg, val_slot in entries:
            if k == key:
                return (val_pg, val_slot)
        return None

    def _find_leaf(self, key: int, current_pgno: int, txn_id: int) -> int:
        data = self.read_page(current_pgno, txn_id)
        page_type, _, _, _, _ = decode_page_header(data)
        
        if page_type == PAGE_LEAF:
            return current_pgno
        elif page_type == PAGE_INTERNAL:
            _, keys, children = self._parse_internal(data)
            # Find the first child where key < keys[i], or use the last child
            for i, k in enumerate(keys):
                if key < k:
                    return self._find_leaf(key, children[i], txn_id)
            return self._find_leaf(key, children[-1], txn_id)
        else:
            raise RuntimeError(f"Invalid page type {page_type} in BTree")

    # --- Insert ---

    def insert(self, key: int, record_page: int, record_slot: int, txn_id: int = None) -> None:
        root_pgno = self.get_root_pgno()
        if root_pgno == 0:
            # Tree empty, create first leaf as root
            root_pgno = self.allocate_page(txn_id)
            leaf_data = self._pack_leaf(root_pgno, 0, [(key, record_page, record_slot)])
            self.write_page(root_pgno, leaf_data, txn_id)
            self.set_root_pgno(root_pgno)
            return

        # Find leaf and keep track of path for parent updates
        path = []
        curr = root_pgno
        while True:
            data = self.read_page(curr, txn_id)
            ptype, _, _, _, _ = decode_page_header(data)
            if ptype == PAGE_LEAF:
                break
            _, keys, children = self._parse_internal(data)
            path.append(curr)
            found = False
            for i, k in enumerate(keys):
                if key < k:
                    curr = children[i]
                    found = True
                    break
            if not found:
                curr = children[-1]

        leaf_pgno = curr
        data = self.read_page(leaf_pgno, txn_id)
        _, next_page, entries = self._parse_leaf(data)
        
        # Insert in sorted order
        entries.append((key, record_page, record_slot))
        entries.sort(key=lambda x: x[0])
        
        if len(entries) <= LEAF_ORDER:
            self.write_page(leaf_pgno, self._pack_leaf(leaf_pgno, next_page, entries), txn_id)
        else:
            # Split leaf
            mid = len(entries) // 2
            left_entries = entries[:mid]
            right_entries = entries[mid:]
            
            new_leaf_pgno = self.allocate_page(txn_id)
            median_key = right_entries[0][0]
            
            # Right leaf becomes the new page, linked after the left leaf
            self.write_page(new_leaf_pgno, self._pack_leaf(new_leaf_pgno, next_page, right_entries), txn_id)
            self.write_page(leaf_pgno, self._pack_leaf(leaf_pgno, new_leaf_pgno, left_entries), txn_id)
            
            self._insert_into_parent(leaf_pgno, median_key, new_leaf_pgno, path, txn_id)

    def _insert_into_parent(self, left_pgno: int, key: int, right_pgno: int, path: list, txn_id: int) -> None:
        if not path:
            # Root split
            new_root = self.allocate_page(txn_id)
            internal_data = self._pack_internal(new_root, [key], [left_pgno, right_pgno])
            self.write_page(new_root, internal_data, txn_id)
            self.set_root_pgno(new_root)
            return
            
        parent_pgno = path.pop()
        data = self.read_page(parent_pgno, txn_id)
        _, keys, children = self._parse_internal(data)
        
        # Insert key and right_pgno at the correct position
        idx = children.index(left_pgno)
        keys.insert(idx, key)
        children.insert(idx + 1, right_pgno)
        
        if len(keys) <= INTERNAL_ORDER:
            self.write_page(parent_pgno, self._pack_internal(parent_pgno, keys, children), txn_id)
        else:
            # Split internal
            mid = len(keys) // 2
            median_key = keys[mid]
            
            left_keys = keys[:mid]
            left_children = children[:mid + 1]
            
            right_keys = keys[mid + 1:]
            right_children = children[mid + 1:]
            
            new_internal_pgno = self.allocate_page(txn_id)
            
            self.write_page(new_internal_pgno, self._pack_internal(new_internal_pgno, right_keys, right_children), txn_id)
            self.write_page(parent_pgno, self._pack_internal(parent_pgno, left_keys, left_children), txn_id)
            
            self._insert_into_parent(parent_pgno, median_key, new_internal_pgno, path, txn_id)
