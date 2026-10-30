"""End-to-end check of cpa-quota-router against a real CLIProxyAPI binary.

    python3 e2e/run_e2e.py /path/to/cli-proxy-api

Nothing leaves the machine: accounts are dummies, the proxy's outbound proxy is
a dead port, and the only upstream is e2e/mock_anthropic.py.

Part 1 runs the router against dummy Claude account files and checks the
priorities it writes through the management API, on disk and on re-runs.
Part 2 checks the proxy behaviour the router relies on: new sessions go to the
highest-priority account, and a session stays on its account after priorities
change.
"""

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROUTER = HERE.parent / "quota_router.py"
MANAGEMENT_KEY = "e2e-management-key"
CLIENT_KEY = "e2e-client-key"


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def request(method, url, headers=None, body=None):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method=method, headers=headers or {})
    if data is not None:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, error.read().decode(errors="replace")


def wait_for(check, what, timeout=20.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            result = check()
            if result:
                return result
        except OSError:
            pass
        time.sleep(0.3)
    raise AssertionError(f"timed out waiting for {what}")


def expect(condition, message):
    if not condition:
        raise AssertionError(message)
    print(f"  ok  {message}")


def iso(hours):
    return (datetime.now(timezone.utc) + timedelta(hours=hours)).isoformat()


def window(utilization, reset_hours):
    return {"utilization": utilization, "resets_at": None if reset_hours is None else iso(reset_hours)}


class Proxy:
    def __init__(self, binary, workdir, config):
        self.port = free_port()
        self.workdir = workdir
        config = config.replace("@PORT@", str(self.port))
        (workdir / "config.yaml").write_text(config)
        self.log = open(workdir / "proxy.log", "w")
        self.process = subprocess.Popen(
            [binary, "-config", str(workdir / "config.yaml")], cwd=workdir, stdout=self.log, stderr=subprocess.STDOUT
        )
        self.base = f"http://127.0.0.1:{self.port}"
        wait_for(lambda: self.auth_files() is not None, "proxy to start")

    def management(self, method, path, body=None):
        return request(method, f"{self.base}/v0/management{path}", {"Authorization": f"Bearer {MANAGEMENT_KEY}"}, body)

    def auth_files(self):
        status, body = self.management("GET", "/auth-files")
        return body["files"] if status == 200 else None

    def message(self, model, session):
        headers = {"x-api-key": CLIENT_KEY, "anthropic-version": "2023-06-01", "X-Claude-Code-Session-Id": session}
        body = {"model": model, "max_tokens": 16, "messages": [{"role": "user", "content": f"hello from {session}"}]}
        return request("POST", f"{self.base}/v1/messages", headers, body)

    def stop(self):
        self.process.terminate()
        self.process.wait(timeout=10)
        self.log.close()


BASE_CONFIG = """config-version: 8
server:
  host: "127.0.0.1"
  port: @PORT@
management:
  allow-remote: false
  secret-key: "{management_key}"
  disable-auto-update-panel: true
access:
  api-keys: ["{client_key}"]
oauth:
  auth-dir: "{auth_dir}"
routing:
  strategy: "round-robin"
  session-affinity: true
  session-affinity-ttl: "1h"
  session-affinity-rebind-on: "quota"
requests:
  proxy-url: "http://127.0.0.1:9"
"""


def run_router(proxy, mock_url, *extra):
    result = subprocess.run(
        [sys.executable, str(ROUTER), "--once", "--management-url", f"{proxy.base}/v0/management",
         "--usage-url", f"{mock_url}/api/oauth/usage", *extra],
        env={**os.environ, "CPA_MANAGEMENT_KEY": MANAGEMENT_KEY}, capture_output=True, text=True, timeout=60,
    )
    if result.returncode != 0:
        raise AssertionError(f"router failed:\n{result.stdout}{result.stderr}")
    return result.stderr


def priorities(proxy):
    return {f["email"]: f.get("priority") for f in proxy.auth_files() if f.get("provider") == "claude"}


def part1_router(binary, mock_url, root):
    print("part 1: router sets priorities through the management API")
    workdir = root / "part1"
    auth_dir = workdir / "auths"
    auth_dir.mkdir(parents=True)
    originals = {}
    for name in "abc":
        record = {
            "type": "claude", "email": f"{name}@e2e.test", "access_token": f"tok-{name}",
            "refresh_token": f"refresh-{name}", "id_token": "", "expired": "2099-01-01T00:00:00Z",
            "last_refresh": "2026-10-30T00:00:00Z", "disabled": False, "account_uuid": f"uuid-{name}",
            "organization_uuid": "org", "organization_name": "e2e",
        }
        path = auth_dir / f"claude-{name}@e2e.test.json"
        path.write_text(json.dumps(record))
        originals[path] = record

    config = BASE_CONFIG.format(management_key=MANAGEMENT_KEY, client_key=CLIENT_KEY, auth_dir=auth_dir)
    proxy = Proxy(binary, workdir, config)
    try:
        wait_for(lambda: len(priorities(proxy)) == 3, "three claude accounts")

        # The live pool on 2026-10-30: a nearly drained account resetting tonight,
        # a fresh one resetting in 4.7 days, and one already used up.
        request("POST", f"{mock_url}/_control/usage", body={
            "tok-a": {"five_hour": window(3, 5), "seven_day": window(96, 12)},
            "tok-b": {"five_hour": window(4, 5), "seven_day": window(19, 112)},
            "tok-c": {"five_hour": window(0, None), "seven_day": window(100, 71)},
        })
        run_router(proxy, mock_url)
        expect(priorities(proxy) == {"a@e2e.test": 20, "b@e2e.test": 10, "c@e2e.test": -10},
               f"soonest weekly reset ranks first, used-up account last: {priorities(proxy)}")

        for path, original in originals.items():
            on_disk = json.loads(path.read_text())
            expect(on_disk.get("priority") == priorities(proxy)[original["email"]],
                   f"{path.name} persists priority {on_disk.get('priority')}")
            expect(all(on_disk.get(k) == original[k] for k in ("access_token", "refresh_token", "email", "type")),
                   f"{path.name} keeps its credentials")

        log = run_router(proxy, mock_url)
        expect("set " not in log and log.count("keep ") == 3, "an unchanged ranking writes nothing")

        # Account a drains; b now resets soonest among the usable ones.
        request("POST", f"{mock_url}/_control/usage", body={
            "tok-a": {"five_hour": window(40, 4), "seven_day": window(100, 11)},
            "tok-b": {"five_hour": window(4, 5), "seven_day": window(19, 112)},
            "tok-c": {"five_hour": window(0, None), "seven_day": window(100, 71)},
        })
        run_router(proxy, mock_url)
        expect(priorities(proxy) == {"a@e2e.test": -10, "b@e2e.test": 10, "c@e2e.test": -10},
               f"a drained account drops to the bottom: {priorities(proxy)}")

        # A usage read that fails leaves the account where it was.
        request("POST", f"{mock_url}/_control/usage", body={
            "tok-a": {"five_hour": window(0, None), "seven_day": window(10, 100)},
            "tok-c": {"five_hour": window(0, None), "seven_day": window(100, 71)},
        })
        run_router(proxy, mock_url)
        expect(priorities(proxy)["b@e2e.test"] == 10, "an account whose usage read fails keeps its priority")

        run_router(proxy, mock_url, "--reset")
        expect(all(p in (None, 0) for p in priorities(proxy).values()), f"--reset clears priorities: {priorities(proxy)}")
    finally:
        proxy.stop()


def part2_selection(binary, mock_url, root):
    print("part 2: proxy sends new sessions to the top priority and keeps bound ones")
    workdir = root / "part2"
    (workdir / "auths").mkdir(parents=True)
    config = BASE_CONFIG.format(management_key=MANAGEMENT_KEY, client_key=CLIENT_KEY, auth_dir=workdir / "auths")
    keys = """api-keys:
  claude:
    - name: mock
      base-url: "{mock_url}"
      proxy-url: "direct"
      keys:
        - api-key: "key-a"
          priority: {a}
        - api-key: "key-b"
          priority: {b}
"""
    config_path = workdir / "config.yaml"
    proxy = Proxy(binary, workdir, config + keys.format(mock_url=mock_url, a=20, b=10))
    try:
        status, models = request("GET", f"{proxy.base}/v1/models", {"x-api-key": CLIENT_KEY})
        model = next(m["id"] for m in models["data"] if m["id"].startswith("claude"))

        def served(session):
            status, body = proxy.message(model, session)
            if status != 200:
                raise AssertionError(f"request failed for {session}: {status} {body}")
            return request("GET", f"{mock_url}/_control/hits")[1][-1]["credential"]

        expect(served("session-1") == "key-a", "a new session goes to the highest priority account (a)")
        expect(served("session-1") == "key-a", "the session stays on a")

        text = config_path.read_text()
        config_path.write_text(text.replace("priority: 20", "priority: 5").replace("priority: 10", "priority: 30"))
        probe = iter(range(1000))
        wait_for(lambda: served(f"probe-{next(probe)}") == "key-b", "priority swap to reach the proxy")
        expect(True, "after the swap a new session goes to b")
        expect(served("session-1") == "key-a", "the session bound before the swap stays on a")
        expect(served("session-2") == "key-b", "another new session also goes to b")
    finally:
        proxy.stop()


def main():
    binary = os.path.abspath(sys.argv[1])
    root = Path(tempfile.mkdtemp(prefix="cpa-quota-router-e2e-"))
    mock_port = free_port()
    mock = subprocess.Popen([sys.executable, "-I", str(HERE / "mock_anthropic.py"), str(mock_port)],
                            stdout=subprocess.PIPE, text=True)
    mock.stdout.readline()
    mock_url = f"http://127.0.0.1:{mock_port}"
    try:
        part1_router(binary, mock_url, root)
        part2_selection(binary, mock_url, root)
    except AssertionError as error:
        print(f"FAIL {error}\nlogs in {root}")
        return 1
    finally:
        mock.terminate()
    print("e2e passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
