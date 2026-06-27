<p align="center">
  <img src="https://img.shields.io/badge/python-3.12-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.12" />
  <img src="https://img.shields.io/badge/zero_dependencies-stdlib_only-2ea44f?style=for-the-badge" alt="Zero Dependencies" />
  <img src="https://img.shields.io/badge/status-under_development-orange?style=for-the-badge" alt="Under Development" />
  <img src="https://img.shields.io/badge/docker-ready-2496ED?style=for-the-badge&logo=docker&logoColor=white" alt="Docker Ready" />
</p>

<h1 align="center">PineDB</h1>

<p align="center">
  <strong>A disk-backed relational storage engine built from scratch in Python.</strong><br/>
  B+Tree indexing · Write-ahead logging · Crash recovery · Atomic transactions · SQL interface<br/>
  <em>Zero external dependencies — stdlib only, by design.</em>
</p>

PineDB is a minimal, educational, relational database management system implemented entirely from scratch in standard Python. It features a custom disk pager, a B+Tree index, write-ahead logging (WAL), and a SQL parser/executor. Its purpose is to demonstrate the fundamental systems concepts underlying durability, crash recovery, and efficient disk I/O in database systems.

---

## Architecture

```
                        ┌──────────────────────────────────┐
                        │           SQL Parser             │
                        │      (parser.py / executor.py)   │
                        │  Tokenizer + recursive descent   │
                        │  SELECT · INSERT · WHERE · CREATE│
                        └───────────────┬──────────────────┘
                                        │ AST
                        ┌───────────────▼──────────────────┐
                        │      Transaction Manager         │
                        │            (txn.py)              │
                        │   BEGIN · COMMIT                 │
                        │   Buffers writes until COMMIT    │
                        └───────────────┬──────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │         B+Tree Index             │
                        │          (btree.py)              │
                        │  On-disk, integer keys           │
                        │  Insert · Point search           │
                        └───────────────┬──────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │    WAL (Durability Layer)        │
                        │          (wal.py)                │
                        │  Append-only log · fsync on      │
                        │  commit · Crash recovery         │
                        └───────────────┬──────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │     Pager (Page I/O Layer)       │
                        │          (pager.py)              │
                        │  4096-byte fixed pages           │
                        │  Direct file I/O                 │
                        └───────────────┬──────────────────┘
                                        │
                                   data.db + data.wal
```

---

## Build and Run

Requirements: Python 3.11+ (no external packages).

```bash
python -m pinedb.main
```

Or specify a custom db path:
```bash
python -m pinedb.main data/mydb.db
```

---

## Crash Recovery Demo

```bash
python -m pinedb.main data/test.db
pinedb> CREATE TABLE t (id INT, name VARCHAR);
pinedb> INSERT INTO t VALUES (1, 'alice');
pinedb> INSERT INTO t VALUES (2, 'bob');
pinedb> EXIT
```

Now simulate a crash — WAL has the data, data.db might not if we had aborted before syncing the data file.
```bash
python -m pinedb.main data/test.db
```
Should print: `[recovery] replayed N page(s) from WAL`
```bash
pinedb> SELECT * FROM t WHERE id = 1;
```
Should return: `{'id': 1, 'name': 'alice'}`

---

## Known Limitations

- B+Tree deletion
- ROLLBACK
- WHERE with >, <, >=, <=
- WHERE with AND / OR
- Multi-column or composite keys
- Multiple indexes per table
- Page cache / LRU buffer pool
- Concurrent transactions / locking
- ALTER TABLE / DROP TABLE

---

## Crash Mid-Write Explanation

If the process crashes mid-write while modifying `data.db` pages, we rely on the Write-Ahead Log (WAL) to restore consistency. Because pages are appended to the WAL and synced to disk *before* any writes to `data.db` occur, a committed transaction is already safely on disk. Upon restart, the recovery process scans the WAL, verifies the checksums of all written pages, and replays all pages belonging to fully committed transactions into `data.db`. Any partially written WAL records or uncommitted transactions are simply ignored and their changes are discarded. This ensures atomic durability despite sudden process termination.
