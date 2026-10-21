"""触发部署（可选）。

站点 al.pelom.cn / cdn.al.pelom.cn 解析到 154.204.253.52，是自建服务，
不是 Cloudflare Pages，所以部署要么打 webhook、要么在服务器上 git pull。

两种都支持：
  * DEPLOY_HOOK 有值  -> POST 一下（Cloudflare/Vercel/自建 CI 的 webhook 都吃这个）
  * DEPLOY_HOOK 为空  -> 只打印提示，由服务器侧定时 pull，不算失败
"""

from __future__ import annotations

import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from common import log, set_output, warn  # noqa: E402


def main() -> int:
    hook = os.environ.get("DEPLOY_HOOK", "").strip()
    if not hook:
        warn("未配置 DEPLOY_HOOK，跳过部署触发（服务器侧可自行 git pull / 定时同步）")
        set_output(deployed=False)
        return 0

    req = urllib.request.Request(hook, method="POST", data=b"{}",
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read(300).decode("utf-8", "replace")
            log(f"部署触发成功：HTTP {resp.status} {body}")
            set_output(deployed=True)
            return 0
    except urllib.error.HTTPError as exc:
        warn(f"部署 webhook 返回 HTTP {exc.code}：{exc.read(200)!r}")
    except Exception as exc:  # noqa: BLE001
        warn(f"部署 webhook 调用失败：{exc}")
    set_output(deployed=False)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
