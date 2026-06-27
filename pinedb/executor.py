import json
import struct
from pinedb.pager import Pager, PAGE_SIZE, PAGE_DATA
from pinedb.wal import WAL
from pinedb.txn import TransactionManager
from pinedb.record import Schema
from pinedb.btree import BPlusTree
from pinedb.parser import CreateTable, InsertInto, Select, BeginTxn, CommitTxn

class Executor:
    def __init__(self, pager: Pager, wal: WAL, txn_mgr: TransactionManager):
        self.pager = pager
        self.wal = wal
        self.txn_mgr = txn_mgr
        self.catalog: dict[str, dict] = {}
        self.trees: dict[str, BPlusTree] = {}
        self._load_catalog()

    def execute(self, node) -> list[dict] | str:
        if isinstance(node, CreateTable):
            return self._handle_create_table(node)
        elif isinstance(node, InsertInto):
            return self._handle_insert(node)
        elif isinstance(node, Select):
            return self._handle_select(node)
        elif isinstance(node, BeginTxn):
            self.txn_mgr.begin()
            return "Transaction started."
        elif isinstance(node, CommitTxn):
            txns = self.txn_mgr.active_txns()
            if not txns:
                return "No active transaction."
            txn_id = txns[0]
            self.txn_mgr.commit(txn_id)
            return "Transaction committed."
        else:
            return "Unknown command."

    def _handle_create_table(self, node: CreateTable) -> str:
        if node.table_name in self.catalog:
            raise ValueError(f"Table {node.table_name} already exists.")

        schema = Schema(node.columns)

        # Allocate leaf page for B+Tree root
        root_pgno = self.pager.allocate_page()
        # Initialize the leaf page
        leaf_header = struct.pack('>B3xIHHI', 1, root_pgno, 0, 0, 0) # 1 = PAGE_LEAF
        leaf_data = leaf_header.ljust(PAGE_SIZE, b'\x00')
        self.pager.write_page(root_pgno, leaf_data)

        # Allocate data page
        data_pgno = self.pager.allocate_page()
        data_header = struct.pack('>B3xIHHI', PAGE_DATA, data_pgno, 0, 0, 0)
        data_page = data_header.ljust(PAGE_SIZE, b'\x00')
        self.pager.write_page(data_pgno, data_page)

        self.catalog[node.table_name] = {
            "name": node.table_name,
            "columns": node.columns,
            "root_pgno": root_pgno,
            "data_head": data_pgno,
            "data_tail": data_pgno,
            "data_tail_slots": 0
        }

        self._persist_catalog()
        return "Table created."

    def _handle_insert(self, node: InsertInto) -> str:
        if node.table_name not in self.catalog:
            raise ValueError(f"Table {node.table_name} not found.")

        meta = self.catalog[node.table_name]
        schema = Schema(meta["columns"])

        if len(node.values) != len(schema.columns):
            raise ValueError("Value count does not match column count.")

        txns = self.txn_mgr.active_txns()
        auto_commit = False
        if not txns:
            txn_id = self.txn_mgr.begin()
            auto_commit = True
        else:
            txn_id = txns[0]

        data_tail_pgno = meta["data_tail"]
        slots = meta["data_tail_slots"]

        max_rows = (PAGE_SIZE - 16) // schema.row_size

        if slots >= max_rows:
            # Allocate new data page
            new_data_pgno = self.pager.allocate_page()
            new_header = struct.pack('>B3xIHHI', PAGE_DATA, new_data_pgno, 0, 0, 0)
            new_page = new_header.ljust(PAGE_SIZE, b'\x00')
            self.txn_mgr.write_page(txn_id, new_data_pgno, new_page)

            # Update old tail's next_page
            old_tail_data = bytearray(self.txn_mgr.read_page(txn_id, data_tail_pgno))
            # next_page is at offset 12 in the 16-byte header
            struct.pack_into('>I', old_tail_data, 12, new_data_pgno)
            self.txn_mgr.write_page(txn_id, data_tail_pgno, bytes(old_tail_data))

            meta["data_tail"] = new_data_pgno
            meta["data_tail_slots"] = 0

            data_tail_pgno = new_data_pgno
            slots = 0

        # Read tail page, insert row
        page_data = bytearray(self.txn_mgr.read_page(txn_id, data_tail_pgno))

        encoded_row = schema.encode(node.values)
        offset = 16 + slots * schema.row_size
        page_data[offset:offset+schema.row_size] = encoded_row

        # Update num_slots
        struct.pack_into('>H', page_data, 8, slots + 1)

        self.txn_mgr.write_page(txn_id, data_tail_pgno, bytes(page_data))

        # Insert into B+Tree
        # We need a B+Tree that reads/writes through the transaction manager for this to be transactional.
        # But for V1, the prompt says: B+Tree must also use txn.write_page() / txn.read_page() for its pages.
        # Let's wrap the pager with a TxnPager for the B+Tree, or just do direct updates if we don't have that.
        # Wait, the prompt says: "B+Tree must also use txn.write_page() / txn.read_page() for its pages."
        # We need to adapt BPlusTree or pass a proxy.

        class TxnPagerProxy:
            def __init__(self, pager, txn_mgr, txn_id):
                self.pager = pager
                self.txn_mgr = txn_mgr
                self.txn_id = txn_id
            def get_page(self, pgno):
                return self.txn_mgr.read_page(self.txn_id, pgno)
            def write_page(self, pgno, data):
                self.txn_mgr.write_page(self.txn_id, pgno, data)
            def allocate_page(self):
                pgno = self.pager.allocate_page()
                # zero page is created by pager, need to buffer it in txn
                self.txn_mgr.write_page(self.txn_id, pgno, b'\x00'*PAGE_SIZE)
                return pgno
            def get_root_pgno(self):
                return self.pager.get_root_pgno()
            def set_root_pgno(self, pgno):
                self.pager.set_root_pgno(pgno)

        tree = BPlusTree(TxnPagerProxy(self.pager, self.txn_mgr, txn_id), meta["root_pgno"])

        # First INT col is the key
        key_idx = -1
        for i, col in enumerate(schema.columns):
            if col[1] == 'INT':
                key_idx = i
                break
        if key_idx == -1:
            raise ValueError("No INT column found for primary key")

        key_val = int(node.values[key_idx])
        tree.insert(key_val, data_tail_pgno, slots)

        # Update catalog meta for root_pgno if tree root changed
        meta["root_pgno"] = tree.root_pgno
        meta["data_tail_slots"] = slots + 1
        self._persist_catalog()

        if auto_commit:
            self.txn_mgr.commit(txn_id)

        return "1 row inserted."

    def _handle_select(self, node: Select) -> list[dict]:
        if node.table_name not in self.catalog:
            raise ValueError(f"Table {node.table_name} not found.")

        meta = self.catalog[node.table_name]
        schema = Schema(meta["columns"])

        # We read directly from pager (committed data) for SELECT
        # Unless we are in a txn, then we should read from txn buffers.
        txns = self.txn_mgr.active_txns()
        txn_id = txns[0] if txns else None

        def read_page(pgno):
            if txn_id is not None:
                return self.txn_mgr.read_page(txn_id, pgno)
            return self.pager.get_page(pgno)

        if node.where_col is not None:
            # Point lookup via B+Tree
            key_val = int(node.where_val)

            class ReadOnlyTxnPagerProxy:
                def __init__(self, pager, txn_mgr, txn_id):
                    self.pager = pager
                    self.txn_mgr = txn_mgr
                    self.txn_id = txn_id
                def get_page(self, pgno):
                    if self.txn_id is not None:
                        return self.txn_mgr.read_page(self.txn_id, pgno)
                    return self.pager.get_page(pgno)
                def get_root_pgno(self):
                    return self.pager.get_root_pgno()

            tree = BPlusTree(ReadOnlyTxnPagerProxy(self.pager, self.txn_mgr, txn_id), meta["root_pgno"])
            result = tree.search(key_val)

            if result is None:
                return []

            page_no, slot = result
            page_data = read_page(page_no)

            offset = 16 + slot * schema.row_size
            row_bytes = page_data[offset:offset+schema.row_size]
            values = schema.decode(row_bytes)
            return [dict(zip(schema.col_names(), values))]

        else:
            # Full scan
            results = []
            curr_pgno = meta["data_head"]
            while curr_pgno != 0:
                page_data = read_page(curr_pgno)
                page_type, _, num_slots, _, next_page = struct.unpack('>B3xIHHI', page_data[:16])
                if page_type != PAGE_DATA:
                    break

                offset = 16
                for _ in range(num_slots):
                    row_bytes = page_data[offset:offset+schema.row_size]
                    values = schema.decode(row_bytes)
                    results.append(dict(zip(schema.col_names(), values)))
                    offset += schema.row_size

                curr_pgno = next_page

            return results

    def _persist_catalog(self) -> None:
        encoded = json.dumps(self.catalog).encode('utf-8')
        padded = encoded.ljust(PAGE_SIZE, b'\x00')[:PAGE_SIZE]

        # If page 1 doesn't exist, allocate it.
        # But wait, allocate_page might change page count.
        while self.pager.page_count() < 2:
            self.pager.allocate_page()

        self.pager.write_page(1, padded)

    def _load_catalog(self) -> None:
        while self.pager.page_count() < 2:
            self.pager.allocate_page()

        data = self.pager.get_page(1).rstrip(b'\x00')
        self.catalog = json.loads(data) if data else {}
