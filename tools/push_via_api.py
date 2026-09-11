# -*- coding: utf-8 -*-
"""无 git 协议网络时的兜底推送：用 GitHub Git Data API 把本地提交重放成远端提交。

背景：本机到 github.com:443 不通（api.github.com 通），`git push` 超时。
本脚本按本地提交逐个重建 blob/tree/commit，保留提交粒度与提交信息，最后一次性移动 main 引用。

用法：python tools/push_via_api.py [commit_sha ...]（缺省=推送 origin/main..HEAD 的全部本地提交）
"""
from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request

OWNER = "strangeyu911-tech"
REPO = "trendforge-v2"
BRANCH = "main"
API = "https://api.github.com"
REPO_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

log: list[str] = []


def w(*a):
    line = " ".join(str(x) for x in a)
    log.append(line)
    print(line)


def git(*args, binary: bool = False):
    p = subprocess.run(["git", "-C", REPO_DIR, *args],
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    if p.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} 失败: {p.stderr.decode('utf-8', 'ignore')}")
    return p.stdout if binary else p.stdout.decode("utf-8", "utf-8-sig").strip()


def get_token() -> str:
    p = subprocess.run(["git", "credential", "fill"], input=b"protocol=https\nhost=github.com\n\n",
                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=REPO_DIR)
    out = p.stdout.decode("utf-8", "ignore")
    for line in out.splitlines():
        if line.startswith("password="):
            return line[len("password="):].strip()
    raise RuntimeError("未能从凭据管理器取到 token")


def api(path: str, method: str = "GET", payload: dict | None = None, token: str = ""):
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    req = urllib.request.Request(API + path, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("Content-Type", "application/json")
    req.add_header("User-Agent", "trendforge-v2-push-script")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            return json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore")
        raise RuntimeError(f"{method} {path} → HTTP {e.code}: {body[:400]}")


def main() -> None:
    token = get_token()
    me = api("/user", token=token)
    w(f"认证为 {me.get('login')}")

    ref = api(f"/repos/{OWNER}/{REPO}/git/ref/heads/{BRANCH}", token=token)
    remote_sha = ref["object"]["sha"]
    w("远端 main 当前：", remote_sha[:8])

    # 关键：本地 refs/remotes/origin/master 可能是陈旧的，必须用 API 拿到的远端 head 做基线，
    # 否则会把「早就在远端上」的老提交又重放一遍（重复历史）。
    shas = sys.argv[1:]
    if not shas:
        shas = git("rev-list", f"{remote_sha}..HEAD").split()
        shas = list(reversed([s.strip() for s in shas if s.strip()]))
    if not shas:
        w("没有需要推送的本地提交")
        return
    w("待重放提交：", [s[:8] for s in shas])

    parent = remote_sha
    for sha in shas:
        message = git("log", "-1", "--format=%B", sha)
        files = [f for f in git("show", "--name-only", "--pretty=format:", sha).splitlines() if f.strip()]
        w("")
        w(f"--- 提交 {sha[:8]} · {len(files)} 个文件 · {message.splitlines()[0][:60]}")

        entries = []
        for f in files:
            listing = git("ls-tree", sha, "--", f)
            if not listing:
                # 该提交里此路径已不存在 → 删除（tree entry 的 sha 置 null）
                entries.append({"path": f, "mode": "100644", "type": "blob", "sha": None})
                w(f"    del  {f}")
                continue
            mode = listing.split()[0]
            raw = git("show", f"{sha}:{f}", binary=True)
            blob = api(f"/repos/{OWNER}/{REPO}/git/blobs", "POST",
                       {"content": base64.b64encode(raw).decode("ascii"), "encoding": "base64"},
                       token=token)
            entries.append({"path": f, "mode": mode, "type": "blob", "sha": blob["sha"]})
            w(f"    blob {f} ({len(raw)}B, mode {mode})")
            time.sleep(0.05)

        tree = api(f"/repos/{OWNER}/{REPO}/git/trees", "POST",
                   {"base_tree": parent, "tree": entries}, token=token)
        commit = api(f"/repos/{OWNER}/{REPO}/git/commits", "POST",
                     {"message": message, "tree": tree["sha"], "parents": [parent]}, token=token)
        w("    → commit", commit["sha"][:8])
        parent = commit["sha"]

    api(f"/repos/{OWNER}/{REPO}/git/refs/heads/{BRANCH}", "PATCH", {"sha": parent}, token=token)
    w("")
    w(f"✅ main 已更新到 {parent[:8]}（共 {len(shas)} 个提交）")

    tag_name = os.environ.get("TAG_NAME", "")
    if tag_name:
        # 先删同名旧 tag（幂等重跑），再创建 annotated tag
        try:
            api(f"/repos/{OWNER}/{REPO}/git/refs/tags/{tag_name}", "DELETE", token=token)
            w(f"    删除同名旧 tag {tag_name}")
        except Exception as e:
            w(f"    旧 tag 不存在或删除失败（忽略）：{str(e)[:80]}")
        tag_obj = api(f"/repos/{OWNER}/{REPO}/git/tags", "POST", {
            "tag": tag_name,
            "message": os.environ.get("TAG_MSG", tag_name),
            "object": parent, "type": "commit",
        }, token=token)
        api(f"/repos/{OWNER}/{REPO}/git/refs", "POST",
            {"ref": f"refs/tags/{tag_name}", "sha": tag_obj["sha"]}, token=token)
        w(f"✅ 已打 tag {tag_name} → {tag_obj['sha'][:8]}")

    report = os.path.join(REPO_DIR, "docs", "data", "PUSH_VIA_API_LOG.md")
    with open(report, "w", encoding="utf-8") as f:
        f.write("# 兜底推送日志（Git Data API）\n\n")
        f.write("\n".join(log))
        f.write("\n")


if __name__ == "__main__":
    main()
