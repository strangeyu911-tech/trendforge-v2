"""一次性脚本：扫描并清洗本地 trendforge_v2.db 中所有 [ev_xxx] 泄漏（与 demo_snapshot.db 对齐）。"""
import sqlite3, re, json, sys

DB = sys.argv[1] if len(sys.argv) > 1 else "src/app/data/trendforge_v2.db"
EV_RE = re.compile(r"\s*\[ev_\d+\]")

con = sqlite3.connect(DB)
cur = con.cursor()

# 找到所有含文本的表/列
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]

total_fixed = 0
for t in tables:
    cur.execute(f"PRAGMA table_info('{t}')")
    cols = [c[1] for c in cur.fetchall()]
    for col in cols:
        # 找出该列中含 [ev_ 的行
        cur.execute(f"SELECT rowid, {col} FROM {t} WHERE {col} LIKE '%[ev_%'")
        rows = cur.fetchall()
        if not rows:
            continue
        for rowid, val in rows:
            if val is None:
                continue
            new = EV_RE.sub("", val)
            if new != val:
                cur.execute(f"UPDATE {t} SET {col}=? WHERE rowid=?", (new, rowid))
                total_fixed += 1
                print(f"[fix] {t}.{col} rowid={rowid}")

con.commit()
print(f"\n已修复 {total_fixed} 处 [ev_] 泄漏于 {DB}")

# 复核：全库再扫一遍
cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [r[0] for r in cur.fetchall()]
remaining = 0
for t in tables:
    cur.execute(f"PRAGMA table_info('{t}')")
    cols = [c[1] for c in cur.fetchall()]
    for col in cols:
        cur.execute(f"SELECT COUNT(*) FROM {t} WHERE {col} LIKE '%[ev_%'")
        c = cur.fetchone()[0]
        if c:
            remaining += c
            print(f"  仍残留 {c} 处 @ {t}.{col}")
con.close()
print("剩余泄漏:", remaining)
