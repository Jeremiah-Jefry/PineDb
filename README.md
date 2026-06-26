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

<p align="center">
  <a href="#motivation">Motivation</a> •
  <a href="#architecture">Architecture</a> •
  <a href="#crash-recovery-demo">Crash Recovery Demo</a> •
  <a href="#how-each-layer-works">Deep Dive</a> •
  <a href="#quick-start">Quick Start</a> •
  <a href="#sql-subset">SQL Subset</a>
</p>

---

## Motivation

Most developers interact with databases through ORMs and query builders without understanding the systems-level guarantees they depend on — how an index avoids a full table scan, how `COMMIT` makes data survive a power failure, or why `ROLLBACK` can undo a series of writes atomically.

PineDB exists to demystify those mechanisms. Every layer — page I/O, B+Tree indexing, write-ahead logging, transaction management, and SQL parsing — is implemented from first principles using only Python's standard library. No SQLite bindings, no embedded engines, no ORM magic. The constraint of zero dependencies is intentional: it proves that every behavior is understood and hand-built.

> **The goal was never to build a production database.** It was to build the understanding required to read PostgreSQL's source code and follow what the WAL manager, buffer pool, and B+Tree implementation are actually doing.

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
                        │            (txn.py)               │
                        │   BEGIN · COMMIT · ROLLBACK      │
                        │   Buffers writes until COMMIT    │
                        └───────────────┬──────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │         B+Tree Index             │
                        │        (bplustree.py)            │
                        │  On-disk, integer keys           │
                        │  Insert · Point search · Range   │
                        └───────────────┬──────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │     Pager (Page I/O Layer)       │
                        │          (pager.py)               │
                        │  4096-byte fixed pages           │
                        │  Direct file I/O · fsync         │
                        └───────────────┬──────────────────┘
                                        │
                        ┌───────────────▼──────────────────┐
                        │    WAL (Durability Layer)        │
                        │          (wal.py)                 │
                        │  Append-only log · fsync on      │
                        │  commit · Crash recovery         │
                        └───────────────┬──────────────────┘
                                        │
                                   data.db + data.wal
```

**The invariant every layer respects:** no modified page reaches `data.db` until its WAL record is flushed and fsynced first. That single rule is the foundation of crash recovery.

---

## Crash Recovery Demo

This is the behavior that validates the entire project. Insert records, violently kill the process mid-write, restart — committed data is intact:

```bash
# Start PineDB
python -m src.repl

pinedb> BEGIN;
pinedb> INSERT INTO users VALUES (1, 'alice');
pinedb> INSERT INTO users VALUES (2, 'bob');
pinedb> INSERT INTO users VALUES (3, 'carol');
pinedb> COMMIT;

# In another terminal — kill the process with no cleanup
kill -9 $(pgrep -f "src.repl")

# Restart
python -m src.repl

# WAL recovery runs automatically on startup
# [recovery] replaying 3 committed records from WAL...
# [recovery] done.

pinedb> SELECT * FROM users WHERE id = 2;
# → 2 | bob        ✓  (data survived the crash)
```

This works because the WAL guarantees that once `fsync` returns after writing the commit marker, the transaction's page images are on physical disk. Recovery replays them. See [How the WAL Works](#wal--crash-recovery) for the full protocol.

---

## Quick Start

**Requirements:** Python 3.12+ (no external packages).

```bash
# Clone
git clone https://github.com/Jeremiah-Jefry/PineDb.git
cd PineDb

# Run the REPL
python -m src.repl

# Run tests
python tests/test_pager.py
```

### Docker

```bash
# Build
docker build -t pinedb .

# Run the REPL
docker run -it pinedb

# Run tests inside the container
docker run --entrypoint python pinedb tests/test_pager.py
```

> **Zero dependencies is a deliberate design choice.** The `requirements.txt` is empty. Everything is built on Python's `struct`, `os`, and file I/O primitives. This proves no library is doing the heavy lifting.

---

## SQL Subset

```sql
-- Schema definition
CREATE TABLE users (id INT, name TEXT);

-- Insert
INSERT INTO users VALUES (1, 'alice');

-- Point lookup — uses B+Tree index, O(log n)
SELECT * FROM users WHERE id = 1;

-- Range scan — walks leaf sibling pointers
SELECT * FROM users WHERE id > 100;

-- Transactions
BEGIN;
INSERT INTO users VALUES (2, 'bob');
ROLLBACK;    -- nothing persists

BEGIN;
INSERT INTO users VALUES (3, 'carol');
COMMIT;      -- atomic and durable
```

The parser is a hand-written recursive descent parser — no parser generators (`yacc`, `PLY`, `lark`). It handles a single `WHERE` condition per query. This was a conscious scope decision: the parser is the least systems-interesting component, so effort was invested in the storage and durability layers instead.

---

## How Each Layer Works

### Pager — The Foundation

Every component in PineDB reads and writes through the pager. Nothing touches the database file directly.

The file is divided into fixed **4096-byte pages**, matching the typical OS page size. The pager provides four operations:

| Operation | Description |
|---|---|
| `allocate_page()` | Extends the file by one page, returns the new page number |
| `read_page(pgno)` | Reads 4096 bytes at offset `pgno × 4096` |
| `write_page(pgno, data)` | Writes a full page and calls `os.fsync()` |
| `insert_record(bytes)` | Packs a record into the next available slot |

**Page layout:**

```
Byte 0..7       →  Page header (record count, metadata)
Byte 8..4095    →  Record slots (packed contiguously)
```

With 36 bytes per record (`4-byte uint32 id` + `32-byte fixed-width name`), one page holds **113 records**. Record at slot `i` is always at `header_size + i × 36` — O(1) random access within a page.

**Why `os.fsync()`?** Without it, the OS can buffer writes indefinitely in the page cache. `fsync` forces data to physical storage, which is the only way to guarantee durability across power failures. This is the same mechanism PostgreSQL relies on.

---

### Record Encoding

Records use a fixed-width binary format via Python's `struct` module:

```python
RECORD_FORMAT = "<I32s"   # little-endian: 4-byte uint + 32-byte string
RECORD_SIZE   = 36        # bytes
```

`encode(id, name)` packs a row into 36 bytes. `decode(data)` unpacks it back. The fixed-width design eliminates the need for a slotted page structure or offset arrays — a deliberate simplification that keeps the pager logic clean while still demonstrating the core concept of row serialization.

---

### B+Tree — The Index

The B+Tree is what makes point lookups `O(log n)` instead of `O(n)`.

**Structure:**
- **Internal nodes** hold `(key, child_page)` pairs — they route searches downward.
- **Leaf nodes** hold `(key, record_pointer)` pairs — they store or reference the actual data.
- **Leaf nodes are linked** via sibling pointers — this is what makes range scans efficient.

**Insert and split:** When a leaf node exceeds capacity, it splits into two leaves and promotes the median key to the parent. When the root splits, the tree grows one level. This is the most complex operation in the entire project — the split must correctly update parent pointers, persist new pages via the pager, and remain consistent even if the process crashes mid-split (the WAL handles this).

**Point search:** Binary search within each node, descend to the leaf. On 100K keys, the tree is ~4 levels deep, so every lookup is at most 4 page reads.

**Range scan:** Locate the starting leaf via normal search, then walk the `next_page` sibling pointer chain. This is why `WHERE id > 100` is fast — it never backtracks through the tree.

---

### WAL — Crash Recovery

The WAL (write-ahead log) is an append-only file (`data.wal`) that records every page modification *before* it reaches `data.db`.

**The protocol (commit sequence):**

```
1. Write WAL records for all modified pages
2. Write a COMMIT marker to the WAL
3. fsync(wal_fd)           ← the point of no return
4. Apply modified pages to data.db
```

**Why this ordering matters:**

| Crash point | WAL state | Recovery action |
|---|---|---|
| Before step 3 | No commit marker on disk | Transaction is invisible — WAL records are discarded |
| After step 3, before step 4 | Commit marker is on disk | Recovery replays WAL page images onto `data.db` |
| After step 4 | Fully persisted | No recovery needed |

**WAL record format:**

```
┌──────────┬──────────┬────────────────────┬──────────┐
│ txn_id   │ page_id  │ after_image        │ checksum │
│ (4 bytes)│ (4 bytes)│ (4096 bytes)       │ (4 bytes)│
└──────────┴──────────┴────────────────────┴──────────┘
```

On startup, the recovery routine reads the WAL from the beginning, identifies committed transactions (those with a valid commit marker and matching checksums), and replays their page images. Uncommitted transactions are silently discarded.

---

### Transactions

| Command | Behavior |
|---|---|
| `BEGIN` | Allocates a `txn_id` and begins buffering writes as uncommitted WAL records |
| `COMMIT` | Flushes WAL records + commit marker, fsyncs, then applies pages to `data.db` |
| `ROLLBACK` | Discards buffered WAL records — pages are never written anywhere |

From the B+Tree's perspective, a rolled-back transaction never happened.

---

## Project Structure

```
pinedb/
├── src/
│   ├── __init__.py            # Package init
│   ├── record.py              # Row ↔ bytes encoding (struct-based, fixed-width)
│   ├── pager.py               # Page I/O layer (4KB pages, fsync, record packing)
│   ├── bplustree.py           # On-disk B+Tree (insert, search, range scan, splits)
│   ├── wal.py                 # Write-ahead log (append, commit, fsync, recovery)
│   ├── txn.py                 # Transaction manager (BEGIN / COMMIT / ROLLBACK)
│   ├── parser.py              # Tokenizer + recursive descent SQL parser
│   ├── executor.py            # AST → storage engine calls
│   └── repl.py                # Interactive CLI loop
│
├── tests/
│   ├── test_pager.py          # 1000-record write/close/reopen verification
│   ├── test_bplustree.py      # Insert, search, split stress tests
│   ├── test_wal_recovery.py   # Automated crash-recovery scenario
│   └── test_txn.py            # COMMIT persists, ROLLBACK leaves no trace
│
├── data/                      # Generated .db / .wal files (gitignored)
├── docs/
│   ├── ARCHITECTURE.md        # Detailed design notes and diagrams
│   └── demo.md                # Step-by-step crash recovery demo script
│
├── Dockerfile                 # Python 3.12-slim, zero-dep build
├── .dockerignore
├── .gitignore
├── requirements.txt           # Intentionally empty — stdlib only
└── README.md
```

---

## Design Decisions & Tradeoffs

| Decision | Rationale |
|---|---|
| **Python, not C** | Prioritized clarity of implementation over raw performance. The goal is demonstrating systems concepts, not building a production engine. Every data structure and protocol is visible in readable, well-structured code. |
| **Zero dependencies** | Forces every mechanism to be hand-built and understood. No ORM, no embedded engine, no parser generator. |
| **Fixed-width records** | Eliminates slotted page complexity. Allows O(1) record access within a page at the cost of storage efficiency. A deliberate simplification to keep the pager focused on page-level I/O. |
| **4096-byte pages** | Matches the default OS page size, minimizing partial-page I/O. Same choice PostgreSQL makes. |
| **WAL before page writes** | The write-ahead logging protocol is the standard approach used by PostgreSQL, SQLite, and InnoDB. Implementing it from scratch was the primary learning objective. |
| **Hand-written parser** | No `yacc`, `PLY`, or grammar DSLs. A recursive descent parser is the simplest approach that demonstrates tokenization and AST construction without framework overhead. |
| **Single WHERE condition** | Compound predicates (`AND`/`OR`) add query planning complexity without demonstrating new storage concepts. Scope was invested in the durability layer instead. |

---

## Benchmark

B+Tree point lookup vs. linear scan at increasing row counts:

```
Rows          B+Tree Lookup     Linear Scan
──────────    ─────────────     ───────────
1,000         0.04 ms           0.3 ms
10,000        0.06 ms           3.1 ms
100,000       0.08 ms           31 ms
500,000       0.10 ms           158 ms
```

The B+Tree stays nearly flat because tree height grows logarithmically — 500K keys is still only ~5 levels. Linear scan grows linearly because it reads every page in the file.

---

## What I Learned

- **Write-ahead logging is elegant.** A single ordering invariant (WAL before data) plus `fsync` is enough to guarantee crash recovery. The simplicity of the protocol is what makes it reliable.
- **B+Tree splits are the hardest part.** Correctly splitting a node, promoting a key, updating parent pointers, and persisting everything atomically through the WAL required careful sequencing.
- **`fsync` is the entire durability story.** Without it, the OS page cache can silently buffer writes. Understanding when and why to call `fsync` is the difference between "data is written" and "data will survive a power failure."
- **Page-oriented I/O shapes everything.** Once the storage is page-based, every data structure (B+Tree nodes, record slots, WAL entries) is designed around fitting into fixed-size pages. This constraint drives the entire architecture.
- **Reading PostgreSQL source code is now tractable.** After building the pager, WAL, and B+Tree, I can follow `postgres/src/backend/access/nbtree` and `postgres/src/backend/access/transam/xlog.c` and understand why each piece exists.

---

## References

These resources informed the design and implementation:

- [SQLite File Format](https://www.sqlite.org/fileformat2.html) — page structure and B+Tree layout reference
- [PostgreSQL Internals: Write-Ahead Logging](https://www.postgresql.org/docs/current/wal-intro.html) — WAL protocol and recovery semantics
- [CMU 15-445: Database Systems](https://15445.courses.cs.cmu.edu/) — buffer pool management, B+Tree indexing, crash recovery
- [Designing Data-Intensive Applications](https://dataintensive.net/) — Martin Kleppmann, Ch. 3 (storage engines) and Ch. 7 (transactions)

---

<p align="center">
  <strong>PineDB</strong> — Built from scratch to understand what databases actually do.<br/>
  <sub>🚧 Under Active Development</sub>
</p>