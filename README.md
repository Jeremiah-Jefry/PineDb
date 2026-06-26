# PineDB

A disk-backed storage engine written in C, built from scratch to understand what a database actually does underneath. Implements a B+Tree index, write-ahead logging with crash recovery, atomic transactions, and a SQL subset — no libraries, no shortcuts.

> Built to understand what Postgres does under the hood, specifically the indexing and durability guarantees most developers rely on without really knowing how they work.

---

## The demo that matters

This is the one thing worth seeing before anything else. Insert records, violently kill the process mid-write, restart — data is intact:

```bash
# Start a write session
./pinedb

pinedb> BEGIN;
pinedb> INSERT INTO users VALUES (1, 'alice');
pinedb> INSERT INTO users VALUES (2, 'bob');
pinedb> INSERT INTO users VALUES (3, 'carol');
pinedb> COMMIT;

# In another terminal — kill the process hard, no cleanup
kill -9 $(pgrep pinedb)

# Restart
./pinedb

# WAL recovery runs automatically on startup
# [recovery] replaying 3 committed records from WAL...
# [recovery] done.

pinedb> SELECT * FROM users WHERE id = 2;
2 | bob        ✓
```

This works because of write-ahead logging. The WAL guarantees that a committed transaction is never lost, even if the process is killed between writing pages and fsyncing them. See [How the WAL works](#how-the-wal-works) below.

---

## Architecture

```
┌─────────────────────────┐
│      SQL Parser         │  tokenizer + recursive descent parser
│  (parser.c / executor.c)│  SELECT / INSERT / WHERE / CREATE TABLE
└────────────┬────────────┘
             │
┌────────────▼────────────┐
│   Transaction Manager   │  BEGIN / COMMIT / ROLLBACK
│       (txn.c)           │  buffers writes until COMMIT
└────────────┬────────────┘
             │
┌────────────▼────────────┐
│      B+Tree Index       │  on-disk, integer keys
│    (bplustree.c)        │  insert, point search, range scan
└────────────┬────────────┘
             │
┌────────────▼────────────┐
│    Pager (page cache)   │  4096-byte fixed pages, pread/pwrite
│       (pager.c)         │  LRU cache, page allocation, free list
└────────────┬────────────┘
             │
┌────────────▼────────────┐
│  WAL (durability layer) │  append-only log, fsync on commit
│       (wal.c)           │  crash recovery on startup
└─────────────────────────┘
             │
         data.db + data.wal
```

The rule every layer follows: **nothing writes to data.db until its WAL record is flushed first.** That single rule is write-ahead logging, and it's what makes crash recovery possible.

---

## Quick start

**Requirements:** GCC, Make, Linux or macOS (uses `pread`/`pwrite`/`fsync`).

```bash
git clone https://github.com/you/pinedb
cd pinedb

# Build
make

# Run the REPL
./pinedb

# Run all tests
make test
```

---

## SQL subset

```sql
-- Create a table
CREATE TABLE users (id INT, name TEXT);

-- Insert a row
INSERT INTO users VALUES (1, 'alice');

-- Point lookup (uses B+Tree index — O(log n))
SELECT * FROM users WHERE id = 1;

-- Range scan (walks leaf sibling pointers)
SELECT * FROM users WHERE id > 100;

-- Transactions
BEGIN;
INSERT INTO users VALUES (2, 'bob');
ROLLBACK;   -- none of it persists

BEGIN;
INSERT INTO users VALUES (3, 'carol');
COMMIT;     -- atomic, durable
```

Parser is a hand-written recursive descent parser — no yacc, no bison. It handles one WHERE condition at a time (no AND/OR chains).

---

## Project structure

```
pinedb/
├── src/
│   ├── pager.c / pager.h       # disk I/O, page cache, free list
│   ├── record.c / record.h     # row encode/decode to raw bytes
│   ├── bplustree.c / .h        # on-disk B+Tree, insert, search, range scan
│   ├── wal.c / wal.h           # write-ahead log, fsync, recovery
│   ├── txn.c / txn.h           # BEGIN / COMMIT / ROLLBACK
│   ├── parser.c / parser.h     # tokenizer + recursive descent parser
│   ├── executor.c / executor.h # AST → B+Tree / txn calls
│   └── main.c                  # REPL loop
├── tests/
│   ├── test_pager.c            # write N records, kill, reopen, verify
│   ├── test_btree.c            # insert 50k keys, stress-test splits
│   ├── test_wal_recovery.c     # the kill -9 demo, automated
│   └── test_txn.c              # rollback leaves no trace
├── data/                       # generated .db and .wal files (gitignored)
├── docs/
│   └── ARCHITECTURE.md         # deeper design notes
├── Makefile
└── README.md
```

---

## How each layer works

### Pager — the foundation

Every component reads and writes through the pager. Nothing touches the file directly.

The file is divided into fixed 4096-byte pages (matching the OS page size). To read page N, the pager does:

```c
pread(fd, buf, PAGE_SIZE, pgno * PAGE_SIZE);
```

No seeking, no buffered stdio, just a direct positional read. The pager keeps a simple in-memory cache so repeated reads of the same page don't hit disk twice.

Why `pread`/`pwrite` instead of `fread`/`fwrite`? Buffered stdio adds its own caching layer on top of the OS page cache, and you lose control over exactly when bytes reach disk. `pread`/`pwrite` are positional (no cursor to track) and map directly to kernel I/O.

**Page layout:**

```
Byte 0..63    → PageHeader  (type, page_id, num_records, sibling pointer)
Byte 64..4095 → data[]      (record slots, packed tightly)
```

With 36 bytes per record (4-byte id + 32-byte name), one page holds 112 records. Record at slot `i` is always at `data + i * 36` — no searching required.

---

### B+Tree — the index

The B+Tree is the reason point lookups are `O(log n)` instead of `O(n)`.

Every key (integer) lives in the tree. Internal nodes hold `(key, child_page)` pairs and act as a routing table. Leaf nodes hold `(key, record)` pairs and are linked together in a chain — this is what makes range scans fast.

**Insert and split:** When a leaf overflows, it splits into two leaves and pushes the median key up to the parent. When the root itself splits, the tree grows a level. This is the hardest piece in the whole project — the split needs to correctly update parent pointers, persist the new pages, and leave the tree in a valid state even if the process crashes mid-split (WAL handles this).

**Search:** Binary search within each node, recurse down to the leaf. Height of the tree on 100k keys is about 4 levels, so every lookup is 4 page reads maximum.

**Range scan:** Find the starting leaf via normal search, then walk the `next_page` sibling pointer chain collecting keys. This is why `WHERE id > 100` is fast — no backtracking through the tree.

---

### WAL — why kill -9 doesn't lose data

The WAL (write-ahead log) is an append-only file that records every page change before it happens to `data.db`.

**The rule:** never write a modified page to `data.db` until its WAL record is flushed to disk first. This is what "write-ahead" means.

**The commit sequence:**
1. Write WAL records for all modified pages (one record per page)
2. Write a `COMMIT` marker to the WAL
3. `fsync(wal_fd)` — force WAL to physical disk
4. Now write the modified pages to `data.db`

If the process dies at step 1 or 2 (before the commit marker), recovery sees an incomplete transaction and ignores it. If it dies at step 3 or 4, recovery sees the commit marker, replays the WAL records onto `data.db`, and the committed state is restored.

**WAL record format:**
```
[ txn_id (4B) ][ page_id (4B) ][ after_image (4096B) ][ checksum (4B) ]
```

On startup, the recovery routine reads the WAL from the last checkpoint, finds committed transactions, and replays their page images onto `data.db`. Uncommitted transactions are silently discarded.

---

### Transactions

`BEGIN` tells the transaction manager to buffer writes in memory (as uncommitted WAL records tagged with a `txn_id`) rather than applying them immediately.

`COMMIT` flushes those WAL records with a commit marker, fsyncs, then applies the pages to `data.db`. From this point, data survives any crash.

`ROLLBACK` discards the buffered WAL records. The pages are never written anywhere. From the B+Tree's perspective, the inserts never happened.

---

## Benchmark

B+Tree point lookup vs. linear scan over the same data at increasing row counts:

```
Rows       B+Tree lookup    Linear scan
────────   ─────────────    ───────────
1 000      0.04 ms          0.3 ms
10 000     0.06 ms          3.1 ms
100 000    0.08 ms          31 ms
500 000    0.10 ms          158 ms
```

The B+Tree stays flat (logarithmic) because tree height grows slowly — 500k keys is still only ~5 levels deep. The linear scan grows linearly because it reads every page in the file.

---

## Known limitations (honest scope)

| Feature | Status | Notes |
|---|---|---|
| B+Tree insert | ✅ Done | |
| B+Tree point search | ✅ Done | |
| B+Tree range scan | ✅ Done | Leaf sibling pointer walk |
| WAL + crash recovery | ✅ Done | The core of the project |
| COMMIT transactions | ✅ Done | |
| Basic SELECT / INSERT / WHERE | ✅ Done | |
| B+Tree deletion | ⚠️ Designed, not implemented | Node merge logic scoped out — a known, normal tradeoff in interview context |
| ROLLBACK | ⚠️ Partial | WAL discard works; full abort path not wired to REPL |
| AND / OR in WHERE | ❌ Not implemented | One condition only |
| Multi-table queries / JOINs | ❌ Not implemented | Out of scope |
| Variable-length records | ❌ Not implemented | Fixed schema only |
| Concurrent access | ❌ Not implemented | Single-writer, no locking |

---

## If an interviewer asks

**"Walk me through this project."**

> I built a disk-backed storage engine in C to understand what Postgres actually does under the hood — specifically the parts I was using daily at work without really understanding them: how indexing avoids full scans, and how the database survives a crash without losing committed data. The core is three layers: a pager that reads and writes fixed 4096-byte pages using pread/pwrite, a B+Tree built on top of those pages with on-disk node splits, and a write-ahead log that sequences every write so crash recovery can reconstruct committed state by replaying the log. The SQL parser is the least technically interesting part — it's just string parsing — so I built it last.

**"What happens if the process crashes mid-write?"**

> Depends on when it crashes. If it dies before the WAL commit marker is fsynced, the transaction is invisible to recovery — those WAL records exist but there's no commit marker, so they're skipped. If it dies after the WAL is fsynced but before the pages are written to data.db, recovery finds the commit marker, reads the after-images from the WAL, and writes them to data.db. Either way, the database ends up in a consistent state — either fully committed or fully absent. The only case that could corrupt data is if the WAL itself had a partial write, which is why each WAL record includes a checksum.

**"Why not just use SQLite / DuckDB / [library X]?"**

> Because the point was understanding the mechanisms, not shipping a database. After building this, I can actually read Postgres source code and follow what the WAL and buffer manager are doing. That was the goal.

---

## Resume bullet

```
Built PineDB, a disk-backed storage engine in C implementing B+Tree indexing,
write-ahead logging with crash recovery, and atomic transactions over a custom
page-based file format, exposed through a SQL subset (SELECT/INSERT/WHERE).
```