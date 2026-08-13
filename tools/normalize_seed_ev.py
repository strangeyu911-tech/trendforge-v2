"""归一化 prompt 模板里的具体证据示例 [ev_001] -> [ev_xxx]（约定占位，应保留）。

只动 prompts.content / prompt_suggestions.new_prompt 这两类「模板列」，
把具体数字令牌 [ev_001] 换成约定文档占位 [ev_xxx]；不动 contents.body
（正文泄漏由 clean_ev 在落库/读时剥离，且本就不该在模板里出现）。
"""
import sqlite3, re, sys

DIG = re.compile(r"\[ev_\d+\]")
TARGETS = [("prompts", "content"), ("prompt_suggestions", "new_prompt")]

for db in sys.argv[1:]:
    con = sqlite3.connect(db)
    cur = con.cursor()
    changed = 0
    for t, c in TARGETS:
        cur.execute(f"SELECT rowid, {c} FROM {t} WHERE {c} LIKE '%[ev_%'")
        for rowid, val in cur.fetchall():
            if not val:
                continue
            new = DIG.sub("[ev_xxx]", val)
            if new != val:
                cur.execute(f"UPDATE {t} SET {c}=? WHERE rowid=?", (new, rowid))
                changed += 1
    con.commit()
    # 复核
    rem = 0
    for t, c in TARGETS:
        cur.execute(f"SELECT {c} FROM {t} WHERE {c} LIKE '%[ev_%'")
        for (v,) in cur.fetchall():
            if v and DIG.search(v):
                rem += 1
    con.close()
    print(f"{db}: 归一化 {changed} 处 | 剩余真实数字泄漏 = {rem}")
