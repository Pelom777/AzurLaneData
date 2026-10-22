"""流水线共用工具。

只用标准库：GitHub 托管 runner 上不需要 pip install，自托管 Windows runner 上
也不需要额外环境（有 Python 3.9+ 即可）。
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path


# ---------------------------------------------------------------- 日志

def log(msg: str) -> None:
    print(f"[azl] {msg}", flush=True)


def warn(msg: str) -> None:
    print(f"::warning::{msg}", flush=True)


def error(msg: str) -> None:
    print(f"::error::{msg}", flush=True)


def die(msg: str, code: int = 1) -> "NoReturn":  # type: ignore[valid-type]
    error(msg)
    sys.exit(code)


# ---------------------------------------------------------------- 子进程

def sh(
    cmd: list[str],
    cwd: str | Path | None = None,
    check: bool = True,
    quiet: bool = False,
    env: dict[str, str] | None = None,
) -> str:
    """跑一个命令，返回 stdout（去掉首尾空白）。"""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    proc = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=full_env,
    )
    if proc.returncode != 0:
        if check:
            sys.stderr.write(proc.stdout or "")
            sys.stderr.write(proc.stderr or "")
            die(f"命令失败（{proc.returncode}）：{' '.join(cmd)}")
        return proc.stdout or ""
    if not quiet and proc.stderr.strip():
        # git 的进度信息走 stderr，原样透出便于排查
        print(proc.stderr.rstrip(), file=sys.stderr, flush=True)
    return (proc.stdout or "").strip()


def git(*args: str, cwd: str | Path | None = None, check: bool = True) -> str:
    return sh(["git", *args], cwd=cwd, check=check)


def pushed(branch: str, cwd: str | Path) -> bool:
    """HEAD 是否已经被远端 ref 包含。

    不能用 `git push` 的返回码判断成功：当远端**已经**包含 HEAD 时，git 会打印
    "Everything up-to-date" 并同样返回 0。也就是说它无法区分
    「我推上去了」和「我的提交被 rebase 弄丢了」——后者在 regen-assets 的
    merge job 里真实发生过一次（run 39641164165 打印了「已推送到 main」，
    但 main 上根本没有那个提交，最后只能从 artifact 恢复 state）。
    """
    cwd = Path(cwd)
    proc = subprocess.run(
        ["git", "ls-remote", "origin", f"refs/heads/{branch}"],
        cwd=str(cwd), capture_output=True, text=True,
        encoding="utf-8", errors="replace",
    )
    if proc.returncode != 0:
        return False
    parts = (proc.stdout or "").split()
    if not parts:
        return False
    head = git("rev-parse", "HEAD", cwd=cwd).strip()
    return parts[0] == head


# ---------------------------------------------------------------- git 对象

def blob_sha1(data: bytes) -> str:
    """算出与 git 完全一致 blob sha，用于"不下载历史文件也能比对内容"。"""
    h = hashlib.sha1()
    h.update(b"blob %d\0" % len(data))
    h.update(data)
    return h.hexdigest()


def file_blob_sha1(path: str | Path) -> str:
    p = Path(path)
    h = hashlib.sha1()
    h.update(b"blob %d\0" % p.stat().st_size)
    with p.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def index_tree(cwd: str | Path, prefix: str) -> dict[str, str]:
    """从 git 索引（不解包文件内容）拿到 prefix 下的 path -> blob_sha。"""
    out = git("ls-tree", "-r", "-z", "HEAD", "--", prefix, cwd=cwd, check=False)
    if not out:
        return {}
    result: dict[str, str] = {}
    for entry in out.split("\0"):
        if not entry:
            continue
        meta, _, path = entry.partition("\t")
        parts = meta.split()
        if len(parts) >= 3 and parts[1] == "blob":
            result[path] = parts[2]
    return result


# ---------------------------------------------------------------- 提交

def commit_and_push(
    cwd: str | Path,
    message: str,
    paths: list[str] | None = None,
    branch: str | None = None,
    push: bool = True,
    paths_from: str | Path | None = None,
) -> bool:
    """只暂存明确列出的路径，避免把"工作区未落盘的历史资产"误判为删除。

    两种模式：
      paths_from  文件清单（NUL 分隔），逐个 add。资产链用这个 ——
                  工作副本里只有少量资产，目录级 `add -A` 会删掉两万多个文件。
      paths       目录/glob 列表，等价于 `add -A -- <paths>`。数据链用这个。

    返回是否真的产生了提交。
    """
    cwd = Path(cwd)
    branch = branch or os.environ.get("GITHUB_REF_NAME") or git(
        "rev-parse", "--abbrev-ref", "HEAD", cwd=cwd
    )

    if paths_from is not None:
        src = Path(paths_from)
        if not src.is_absolute():
            src = Path(cwd) / src
        if not src.exists() or src.stat().st_size == 0:
            log("文件清单为空，跳过提交")
            return False
        git("add", "--pathspec-from-file", str(src), "--pathspec-file-nul", cwd=cwd)
    elif paths:
        git("add", "-A", "--", *paths, cwd=cwd)
    else:
        raise ValueError("paths 与 paths_from 至少要给一个")

    staged = git("diff", "--cached", "--name-only", cwd=cwd)
    if not staged:
        log("没有内容变化，跳过提交")
        return False

    changed = staged.splitlines()
    log(f"变更 {len(changed)} 个文件，示例：{changed[:5]}")
    git("-c", "user.name=github-actions[bot]",
        "-c", "user.email=41898282+github-actions[bot]@users.noreply.github.com",
        "commit", "-q", "-m", message, cwd=cwd)

    if push:
        for attempt in range(1, 4):
            proc = subprocess.run(
                ["git", "push", "-q", "origin", f"HEAD:{branch}"],
                cwd=str(cwd),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if proc.returncode == 0 and pushed(branch, cwd):
                log(f"已推送到 {branch}（已回读远端校验）")
                return True
            if proc.returncode == 0:
                # 返回 0 但远端不含本次提交 = 提交在 rebase 里被丢掉了
                warn(f"推送返回 0，但远端 {branch} 不含本次提交（{attempt}/3），重试")
            else:
                warn(f"推送失败（{attempt}/3）：{(proc.stderr or '').strip()[:400]}")
            if attempt == 3:
                die(f"推送后校验未通过：远端 {branch} 不含本次提交，请人工检查")
            git("pull", "--rebase", "--autostash", "-q", "origin", branch,
                cwd=cwd, check=False)
    return True


def write_json(path: str | Path, obj) -> None:
    """稳定格式写 JSON：键排序 + 不转义中文，保证 diff 只反映真实变化。"""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(p.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8", newline="\n") as fh:
        json.dump(obj, fh, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        fh.write("\n")
    tmp.replace(p)


def set_output(**kwargs) -> None:
    """写到 $GITHUB_OUTPUT，供后续 step 读取。"""
    out = os.environ.get("GITHUB_OUTPUT")
    if not out:
        for k, v in kwargs.items():
            log(f"output {k}={v}")
        return
    with open(out, "a", encoding="utf-8") as fh:
        for k, v in kwargs.items():
            v = str(v).lower() if isinstance(v, bool) else v
            fh.write(f"{k}={v}\n")


def append_summary(markdown: str) -> None:
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if path:
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(markdown + "\n")
    else:
        log(markdown)
