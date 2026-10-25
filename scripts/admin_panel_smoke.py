#!/usr/bin/env python3
"""Read-only admin UI checks via agent-browser; no Swarm task or training mutations.

Usage: python3 scripts/admin_panel_smoke.py --url http://127.0.0.1:5174 --out /tmp/admin-qa
Requires a running application and the installed agent-browser CLI. Raw snapshots stay in --out.
"""

import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:5174")
    parser.add_argument("--out", default="/tmp/izge-admin-ui-smoke")
    args = parser.parse_args()
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, "AGENT_BROWSER_SESSION": "izge-admin-smoke"}
    results = []

    def browser(*command):
        result = subprocess.run(
            ["agent-browser", *command],
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=45,
        )
        with (out / "browser.log").open("a") as log:
            log.write(" ".join(command) + "\n" + result.stdout + "\n")
        if result.returncode:
            raise RuntimeError(f"Browser command failed: {command[0]}")
        return result.stdout

    try:
        origin = urlsplit(args.url)
        for source, expected in [(f"{origin.scheme}://{origin.netloc}", 422), ("https://evil.test", 403)]:
            # Invalid input cannot create a task. This catches dev-proxy Host/Origin regressions.
            req = Request(
                args.url.rstrip("/") + "/api/admin/tasks",
                data=b"{}",
                headers={"Content-Type": "application/json", "Origin": source},
            )
            try:
                with urlopen(req, timeout=10) as response:
                    status = response.status
            except HTTPError as exc:
                status = exc.code
            if status != expected:
                raise RuntimeError(f"Origin guard: expected {expected}, received {status}")
        results.append(dict(page="origin-guard", result="PASS"))
        browser("open", args.url)
        browser("set", "viewport", "1280", "900")
        browser("wait", '.shell[aria-busy="false"]')
        browser("wait", "--text", "ML deneyleri")
        for name, heading, check, filename in [
            ("◈ ML deneyleri", "Deney arşivi", "Model / deney", "ml"),
            ("▦ Proje yönetimi", "İş panosu", "Backlog", "project"),
            ("⌁ Ajan operasyonları", "Ajan çalışma alanı", "Claude / Codex", "operations"),
            ("◉ Saha izleme", "İşletme ve video hattı", "Video iş kuyruğu", "monitoring"),
        ]:
            snapshot = browser("snapshot", "-i")
            match = re.search(r'button "' + re.escape(name) + r'" \[ref=(\w+)\]', snapshot)
            if not match:
                raise RuntimeError(f"Navigation missing: {name}")
            browser("click", "@" + match[1])
            browser("wait", "--text", heading)
            browser("wait", '.admin-page[aria-busy="false"]')
            body = browser("get", "text", "body")
            if check not in body:
                raise RuntimeError(f"Page content missing: {heading}")
            browser("screenshot", str(out / f"{filename}.png"))
            results.append(dict(page=filename, result="PASS"))
            if filename == "ml":
                snapshot = browser("snapshot", "-i")
                smoke = re.search(r'button "smoke" \[ref=(\w+)\]', snapshot)
                if smoke:
                    browser("click", "@" + smoke[1])
                    browser("wait", "--text", "Epoch bazında kayıp")
                    browser("screenshot", str(out / "ml-loss.png"))
                    results.append(dict(page="real-smoke-loss-chart", result="PASS"))
            browser("set", "viewport", "390", "844")
            overflow = browser("eval", "document.documentElement.scrollWidth > window.innerWidth")
            if "true" in overflow:
                browser("screenshot", str(out / f"{filename}-overflow.png"))
                browser("eval", "JSON.stringify([...document.querySelectorAll('*')].filter(e => e.getBoundingClientRect().right > innerWidth + 1).map(e => ({tag:e.tagName, cls:e.className, width:Math.round(e.getBoundingClientRect().width)})).slice(0,30))")
                raise RuntimeError(f"Mobile document overflows horizontally: {filename}")
            browser("screenshot", str(out / f"{filename}-mobile.png"))
            results.append(dict(page=filename + "-mobile", result="PASS"))
            browser("set", "viewport", "1280", "900")
        (out / "results.json").write_text(json.dumps(results, indent=2))
        print(f"PASS admin UI: {len(results)} checks; evidence: {out}")
        return 0
    except (RuntimeError, subprocess.TimeoutExpired) as exc:
        print(f"FAIL admin UI: {exc}; log: {out / 'browser.log'}")
        return 1
    finally:
        browser("close")


if __name__ == "__main__":
    sys.exit(main())
