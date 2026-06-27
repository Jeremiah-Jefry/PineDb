"""
executor.py — Layer 7: Executor

Ast -> BTree / Transaction Manager calls
"""

import json
from pinedb.pager import Pager, PAGE_DATA, encode_page_header, decode_page_header, PAGE_SIZE
from pinedb.wal import WAL
from pinedb.txn import TransactionManager
from pinedb.btree import BPlusTree
from pinedb.record import Schema
from pinedb.parser import CreateTable, InsertInto, Select, BeginTxn, CommitTxn

CATALOG_PGNO = 1

class Executor:
    def __init__(self, pager: Pager, wal: WAL, txn_mgr: TransactionManager):
        self.pager = pager
        self.wal = wal
        self.txn_mgr = txn_mgr
        self.active_txn = None
        
        # Ensure file has catalog page
        while self.pager.page_count() <= CATALOG_PGNO:
            self.pager.allocate_page()
            
        self.catalog = self._load_catalog() # table_name -> {'schema': Schema, 'root_pgno': int}
        
    def _load_catalog(self):
        data = self.pager.get_page(CATALOG_PGNO)
        # We can use a simple JSON encoding padded with 0s for the catalog in V1
        content = data.rstrip(b'\x00')
        if not content:
            return {}
            
        try:
            raw_catalog = json.loads(content.decode('utf-8'))
            catalog = {}
            for table_name, info in raw_catalog.items():
                catalog[table_name] = {
                    'schema': Schema(info['columns']),
                    'root_pgno': info['root_pgno']
                }
            return catalog
        except Exception:
            return {}
            
    def _save_catalog(self, txn_id=None):
        raw_catalog = {}
        for table_name, info in self.catalog.items():
            raw_catalog[table_name] = {
                'columns': info['schema'].columns,
                'root_pgno': info['root_pgno']
            }
            
        content = json.dumps(raw_catalog).encode('utf-8')
        if len(content) > PAGE_SIZE:
            raise RuntimeError("Catalog too large for single page in V1")
        
        content = content.ljust(PAGE_SIZE, b'\x00')
            
        if txn_id is not None:
            self.txn_mgr.write_page(txn_id, CATALOG_PGNO, content)
        else:
            self.pager.write_page(CATALOG_PGNO, content)
            
    def _get_txn(self):
        if self.active_txn is not None:
            return self.active_txn, False # False means it's part of a larger explicit txn
        # Auto-commit transaction
        return self.txn_mgr.begin(), True

    def execute(self, ast) -> list[dict] | str:
        if isinstance(ast, BeginTxn):
            if self.active_txn is not None:
                raise RuntimeError("Transaction already active")
            self.active_txn = self.txn_mgr.begin()
            return "Transaction started"
            
        elif isinstance(ast, CommitTxn):
            if self.active_txn is None:
                raise RuntimeError("No active transaction to commit")
            self.txn_mgr.commit(self.active_txn)
            self.active_txn = None
            return "Transaction committed"
            
        elif isinstance(ast, CreateTable):
            return self._execute_create_table(ast)
            
        elif isinstance(ast, InsertInto):
            return self._execute_insert_into(ast)
            
        elif isinstance(ast, Select):
            return self._execute_select(ast)
            
        raise NotImplementedError(f"Unsupported AST node: {type(ast)}")

    def _execute_create_table(self, ast: CreateTable):
        if ast.table_name in self.catalog:
            raise RuntimeError(f"Table {ast.table_name} already exists")
            
        txn_id, auto_commit = self._get_txn()
        
        # BTree uses the pager for allocating.
        # But wait, BPlusTree uses pager.set_root_pgno() which modifies page 0.
        # For multiple tables, we shouldn't use page 0 root_pgno.
        # The spec says: "Allocate a new root page for this table's B+Tree... Persist catalog"
        btree = BPlusTree(self.pager, self.txn_mgr)
        root_pgno = btree.allocate_page(txn_id)
        
        empty_leaf = btree._pack_leaf(root_pgno, 0, [])
        btree.write_page(root_pgno, empty_leaf, txn_id)
        
        self.catalog[ast.table_name] = {
            'schema': Schema(ast.columns),
            'root_pgno': root_pgno
        }
        self._save_catalog(txn_id)
        
        if auto_commit:
            self.txn_mgr.commit(txn_id)
            
        return f"Table {ast.table_name} created"

    def _execute_insert_into(self, ast: InsertInto):
        if ast.table_name not in self.catalog:
            raise RuntimeError(f"Table {ast.table_name} does not exist")
            
        info = self.catalog[ast.table_name]
        schema = info['schema']
        root_pgno = info['root_pgno']
        
        txn_id, auto_commit = self._get_txn()
        
        encoded_row = schema.encode(ast.values)
        
        # Find a data page with space, or allocate a new one.
        # For V1, we can just do a very simple scan of all data pages.
        # But wait, scanning all pages is slow. The spec says: "allocate new data page when current one is full".
        # Let's just track a 'last_data_pgno' or search backwards from pager.page_count().
        data_pgno = None
        for pg in range(self.pager.page_count() - 1, 1, -1):
            page_data = self.txn_mgr.read_page(txn_id, pg)
            ptype, _, num_slots, _, next_page = decode_page_header(page_data)
            if ptype == PAGE_DATA:
                max_slots = (PAGE_SIZE - 16) // schema.row_size
                if num_slots < max_slots:
                    data_pgno = pg
                    break
                    
        if data_pgno is None:
            data_pgno = self.pager.allocate_page()
            # Ensure dirty
            header = encode_page_header(PAGE_DATA, data_pgno, 0, 0)
            self.txn_mgr.write_page(txn_id, data_pgno, header)
            
        # Read the page
        page_data = bytearray(self.txn_mgr.read_page(txn_id, data_pgno))
        _, _, num_slots, _, _ = decode_page_header(page_data)
        
        # Write row to slot
        slot = num_slots
        offset = 16 + slot * schema.row_size
        page_data[offset:offset + schema.row_size] = encoded_row
        
        # Update header
        new_header = encode_page_header(PAGE_DATA, data_pgno, num_slots + 1, 0)
        page_data[:16] = new_header
        
        self.txn_mgr.write_page(txn_id, data_pgno, bytes(page_data))
        
        # Get key for BTree (assume first column is the key, and it's INT)
        key_val = ast.values[0]
        
        # Subclass BPlusTree to override root_pgno fetching since it's per table now
        class TableBTree(BPlusTree):
            def get_root_pgno(self):
                return root_pgno
            def set_root_pgno(self, pgno):
                info['root_pgno'] = pgno
                # In a real system, we'd dirty the catalog page here.
                # Since _save_catalog is called right below, it's fine.
                
        btree = TableBTree(self.pager, self.txn_mgr)
        btree.insert(key_val, data_pgno, slot, txn_id)
        
        # Save catalog if root changed
        self._save_catalog(txn_id)
        
        if auto_commit:
            self.txn_mgr.commit(txn_id)
            
        return "1 row inserted"

    def _execute_select(self, ast: Select):
        if ast.table_name not in self.catalog:
            raise RuntimeError(f"Table {ast.table_name} does not exist")
            
        info = self.catalog[ast.table_name]
        schema = info['schema']
        root_pgno = info['root_pgno']
        
        txn_id = self.active_txn
        # If no active txn, we can still read via pager directly, but better to use read_page with None
        
        class TableBTree(BPlusTree):
            def get_root_pgno(self):
                return root_pgno
                
        btree = TableBTree(self.pager, self.txn_mgr)
        
        results = []
        if ast.where_col is not None:
            # Point lookup
            if ast.where_col != schema.columns[0][0]:
                raise RuntimeError("V1 only supports WHERE on the primary key (first column)")
                
            res = btree.search(ast.where_val, txn_id)
            if res is not None:
                data_pgno, slot = res
                page_data = btree.read_page(data_pgno, txn_id)
                offset = 16 + slot * schema.row_size
                row_data = page_data[offset:offset + schema.row_size]
                values = schema.decode(row_data)
                
                row_dict = {}
                for i, (col_name, _) in enumerate(schema.columns):
                    row_dict[col_name] = values[i]
                results.append(row_dict)
        else:
            # Full scan: Walk all data pages (slow but simple for V1)
            # Actually, the spec says "walk leaf pages from leftmost leaf" for BTree.
            # Let's walk the BTree leaves.
            curr = root_pgno
            while True:
                data = btree.read_page(curr, txn_id)
                ptype, _, num_slots, _, next_page = decode_page_header(data)
                if ptype == PAGE_DATA or ptype == PAGE_FREE: 
                    # Should not reach here for BTree root
                    break
                if ptype == PAGE_LEAF:
                    break
                _, _, children = btree._parse_internal(data)
                curr = children[0] # Go leftmost
                
            leaf_pgno = curr
            while leaf_pgno != 0:
                data = btree.read_page(leaf_pgno, txn_id)
                ptype, _, _, next_page = decode_page_header(data)[:4] # _parse_leaf expects correct unpack
                _, next_page, entries = btree._parse_leaf(data)
                
                for key, data_pgno, slot in entries:
                    page_data = btree.read_page(data_pgno, txn_id)
                    offset = 16 + slot * schema.row_size
                    row_data = page_data[offset:offset + schema.row_size]
                    values = schema.decode(row_data)
                    
                    row_dict = {}
                    for i, (col_name, _) in enumerate(schema.columns):
                        row_dict[col_name] = values[i]
                    results.append(row_dict)
                    
                leaf_pgno = next_page
                
        return results
