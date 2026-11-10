from __future__ import annotations

import argparse
import base64
import datetime as dt
import hashlib
import io
import json
import os
import re
import shutil
import signal
import subprocess
import tarfile
import threading
import time
import urllib.request
from pathlib import Path
from typing import Any

CLAUDE_URL = (
    "https://registry.npmjs.org/@anthropic-ai/claude-code-linux-x64/-/claude-code-linux-x64-2.1.159.tgz"
)
CLAUDE_SHA512 = "EvB0y/2l1qHYV8lLpltiMKhWaTEui51A1Uh1wNqmvZowMnQx0abIahfm5Dc/AE295fCISF0ySi2vZCvnHiPnsQ=="
CLAUDE = Path("/opt/claude/claude")
RUNNER = "runner"
BUILDER = "builder"
WORKSPACES = Path("/home/runner/ws")
HOMES = Path("/home/runner/h")
VENVS = Path("/opt/venvs")
UV_PYTHON = Path("/opt/uv/python")
ARMS = (("A", "http://127.0.0.1:8081", "router off"), ("B", "http://127.0.0.1:8082", "router on"))
TOOLS = "Read,Edit,Write,Glob,Grep,Bash(python -m pytest:*)"
COMPACT_WINDOW = os.environ.get("LIVE_COMPACT_WINDOW", "133000")
COMPACT_PCT = os.environ.get("LIVE_COMPACT_PCT", "")
COMPACT_MAX_TOKENS = 20000
MT_ROUTE = "microtensor/ctx_compress"
RESULT = re.compile(r"^(PASSED|FAILED|ERROR|XFAIL|XPASS|SKIPPED)\s+(\S.*?)(?:\s+-\s+.*)?$")
PROMPT = (
    "You are working in a Python repository checked out in the current directory. "
    "Resolve the issue below by changing the source code. Hidden tests will be run after you "
    "finish, so make the fix complete and keep existing behaviour working. You can run the test "
    "suite with `python -m pytest` followed by paths or -k filters.\n\n<issue>\n{issue}\n</issue>"
)


def now() -> str:
    return dt.datetime.now(dt.UTC).strftime("%H:%M:%S")


def say(text: str) -> None:
    print(f"{now()} {text}", flush=True)


def clean(home: str, extra: dict[str, str] | None = None) -> dict[str, str]:
    return {
        "HOME": home,
        "PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
        "LANG": "C.UTF-8",
        **(extra or {}),
    }


def sh(
    command: str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    timeout: float = 1800,
    user: str = "",
) -> subprocess.CompletedProcess[str]:
    if user:
        values = clean(f"/home/{user}", env)
        cwd = cwd or Path(f"/home/{user}")
        argv = [
            "runuser",
            "-u",
            user,
            "--",
            "env",
            "-i",
            *[f"{k}={v}" for k, v in values.items()],
            "bash",
            "-c",
            command,
        ]
        return subprocess.run(argv, cwd=cwd, capture_output=True, text=True, timeout=timeout, check=False)
    return subprocess.run(
        ["bash", "-c", command],
        cwd=cwd,
        env=clean("/root", env),
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )


def must(command: str, **kwargs: Any) -> str:
    done = sh(command, **kwargs)
    if done.returncode != 0:
        raise RuntimeError(f"{command[:160]} failed: {(done.stdout + done.stderr)[-1500:]}")
    return done.stdout


def install_claude() -> None:
    if CLAUDE.is_file():
        return
    raw = urllib.request.urlopen(CLAUDE_URL, timeout=600).read()
    if base64.b64encode(hashlib.sha512(raw).digest()).decode() != CLAUDE_SHA512:
        raise SystemExit("the Claude Code tarball does not match its npm integrity hash")
    CLAUDE.parent.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(raw), mode="r:gz") as archive:
        member = archive.extractfile("package/claude")
        if member is None:
            raise SystemExit("the tarball holds no claude binary")
        CLAUDE.write_bytes(member.read())
    CLAUDE.chmod(0o755)
    say("claude " + must(f"{CLAUDE} --version").strip())


def setup() -> None:
    for user in (RUNNER, BUILDER):
        must(f"id {user} >/dev/null 2>&1 || useradd -m -s /bin/bash {user}")
    must("git config --global --add safe.directory '*'")
    must(
        "command -v uv >/dev/null 2>&1 || (curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh)"
    )
    for path in (WORKSPACES, HOMES, VENVS, UV_PYTHON):
        path.mkdir(parents=True, exist_ok=True)
    must(
        f"chown -R {RUNNER}:{RUNNER} /home/{RUNNER} && chmod 755 /home/{RUNNER} && chown -R {BUILDER}:{BUILDER} {VENVS} {UV_PYTHON} && chmod 755 {VENVS} {UV_PYTHON.parent} && chmod 700 /opt/pilot /work"
    )
    for secret in ("/etc/rp_environment",):
        if Path(secret).exists():
            os.chmod(secret, 0o600)
    install_claude()


def install_steps(task: dict[str, Any]) -> list[str]:
    pinned = task["instance_id"].split("__")[0] in ("hgrecco", "sqlfluff", "tobymao")
    steps = task["install_commands_pinned"] if pinned else task["install_commands"]
    found = []
    for step in steps:
        if " -m venv " in step or step.startswith("apt-get"):
            continue
        if step.startswith("write requirements_frozen_lines"):
            Path("/tmp/requirements_frozen.txt").write_text(
                "\n".join(task["requirements_frozen_lines"]) + "\n"
            )
            continue
        found.append(step)
    return found


def prepare(task: dict[str, Any]) -> tuple[Path, Path]:
    name = task["instance_id"]
    workspace = WORKSPACES / name
    venv = VENVS / name
    if not (workspace / ".git").is_dir():
        workspace.mkdir(parents=True, exist_ok=True)
        must(f"chown {RUNNER}:{RUNNER} {workspace}")
        must(
            f"git init -q && git remote add origin https://github.com/{task['repo']}.git && git fetch -q --depth 1 origin {task['base_commit']} && git checkout -q FETCH_HEAD",
            cwd=workspace,
            user=RUNNER,
            timeout=1800,
        )
    if not (venv / "bin" / "python").is_file():
        must(f"chown -R {BUILDER}:{BUILDER} {workspace}")
        must(
            f"uv venv -q --seed --python {task['python']} {venv}",
            user=BUILDER,
            env={"UV_PYTHON_INSTALL_DIR": str(UV_PYTHON)},
            timeout=1200,
        )
        env = {
            "PATH": f"{venv}/bin:/usr/local/bin:/usr/bin:/bin",
            "VIRTUAL_ENV": str(venv),
            "PIP_DISABLE_PIP_VERSION_CHECK": "1",
        }
        for step in install_steps(task):
            done = sh(step, cwd=workspace, env=env, timeout=2400, user=BUILDER)
            say(f"  install {step[:90]} -> {done.returncode}")
            if done.returncode != 0:
                say("    " + (done.stdout + done.stderr)[-600:].replace("\n", " | "))
        must(f"chmod -R a+rX {venv} {UV_PYTHON} && chown -R {RUNNER}:{RUNNER} {workspace}")
    return workspace, venv


def reset(task: dict[str, Any], workspace: Path) -> None:
    must(
        f"git reset -q --hard {task['base_commit']} && git clean -q -fd && find . -name __pycache__ -type d -prune -exec rm -rf {{}} +",
        cwd=workspace,
    )
    must(f"chown -R {RUNNER}:{RUNNER} {workspace}")


def apply(patch: str, workspace: Path) -> bool:
    target = Path("/tmp/patch.diff")
    target.write_text(patch)
    for flags in ("", "--3way", "--reject"):
        if sh(f"git apply {flags} /tmp/patch.diff", cwd=workspace).returncode == 0:
            return True
    return False


def run_tests(task: dict[str, Any], workspace: Path, venv: Path) -> dict[str, str]:
    command = f"export PATH={venv}/bin:$PATH; cd {workspace}; {task['test_command']}"
    done = sh(command, user=RUNNER, timeout=1200)
    found: dict[str, str] = {}
    for line in (done.stdout + "\n" + done.stderr).splitlines():
        hit = RESULT.match(line.strip())
        if hit:
            found[hit.group(2).strip()] = hit.group(1)
    return found


def passed(results: dict[str, str], wanted: list[str], truncated: bool = False) -> list[str]:
    missing = []
    for test in wanted:
        status = results.get(test)
        if status is None and truncated:
            status = next((v for k, v in results.items() if k.startswith(test)), None)
        if status not in ("PASSED", "XFAIL"):
            missing.append(test)
    return missing


def hidden_files(task: dict[str, Any]) -> list[str]:
    return [line[6:].strip() for line in task["test_patch"].splitlines() if line.startswith("+++ b/")]


def grade(task: dict[str, Any], workspace: Path, venv: Path, gold_p2p: list[str]) -> dict[str, Any]:
    for path in hidden_files(task):
        if sh(f"git cat-file -e {task['base_commit']}:{path}", cwd=workspace).returncode == 0:
            sh(f"git checkout {task['base_commit']} -- {path}", cwd=workspace)
        else:
            (workspace / path).unlink(missing_ok=True)
    applied = apply(task["test_patch"], workspace)
    must(f"chown -R {RUNNER}:{RUNNER} {workspace}")
    results = run_tests(task, workspace, venv) if applied else {}
    f2p = passed(results, task["FAIL_TO_PASS"])
    p2p = passed(results, gold_p2p, truncated=True)
    return {
        "hidden_applied": applied,
        "f2p_failed": f2p,
        "p2p_failed": p2p,
        "passed": applied and not f2p and not p2p,
    }


def validate(task: dict[str, Any], workspace: Path, venv: Path) -> list[str] | None:
    reset(task, workspace)
    if not apply(task["test_patch"], workspace):
        say("  the hidden tests do not apply")
        return None
    before = run_tests(task, workspace, venv)
    if not passed(before, task["FAIL_TO_PASS"]):
        say("  the failing tests already pass without the fix")
        return None
    if not apply(task["patch"], workspace):
        say("  the reference fix does not apply")
        return None
    after = run_tests(task, workspace, venv)
    still = passed(after, task["FAIL_TO_PASS"])
    reset(task, workspace)
    if still:
        say(f"  {len(still)} failing tests still fail with the reference fix: {still[:3]}")
        return None
    gold = [t for t in task["PASS_TO_PASS"] if not passed(after, [t], truncated=True)]
    say(
        f"  valid: {len(task['FAIL_TO_PASS'])} failing tests fixed by the reference, {len(gold)} of {len(task['PASS_TO_PASS'])} passing tests kept"
    )
    return gold


def relay_rows(log: Path, step: str) -> list[dict[str, Any]]:
    if not log.is_file():
        return []
    rows = []
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if row.get("step") == step:
                rows.append(row)
    return rows


def spent(log: Path) -> float:
    if not log.is_file():
        return 0.0
    total = 0
    for line in log.read_text(encoding="utf-8").splitlines():
        if line.strip():
            row = json.loads(line)
            if str(row.get("step", "")).startswith("live/"):
                total += int(row.get("cost_usd_micros") or 0)
    return total / 1e6


def is_compaction(row: dict[str, Any]) -> bool:
    return row.get("route_id") == MT_ROUTE or (row.get("shape") or {}).get("max_tokens") == COMPACT_MAX_TOKENS


def card_serving() -> bool:
    hotkey = os.environ.get("MT_HOTKEY_SS58", "")
    try:
        found = json.loads(
            urllib.request.urlopen(
                f"https://api.microtensor.cloud/v1/public/inference/providers/{hotkey}", timeout=30
            ).read()
        )
    except (OSError, ValueError):
        return False
    return any(g.get("status") == "serving" and g.get("worker") == "pod" for g in found.get("gpus", []))


def wait_for_card(limit: float = 3600) -> bool:
    deadline = time.time() + limit
    while time.time() < deadline:
        if card_serving():
            return True
        say("  waiting for the ctx_compress card to serve")
        time.sleep(60)
    return False


def session(
    task_id: str,
    arm: str,
    url: str,
    model: str,
    cwd: Path,
    venv: Path,
    prompt: str,
    args: argparse.Namespace,
    log: Path,
) -> dict[str, Any]:
    home = HOMES / f"{task_id}-{arm}"
    shutil.rmtree(home, ignore_errors=True)
    home.mkdir(parents=True)
    must(f"chown -R {RUNNER}:{RUNNER} {home}")
    step = f"live/{task_id}/{arm}"
    env = {
        "HOME": str(home),
        "PATH": f"{venv}/bin:/usr/local/bin:/usr/bin:/bin",
        "LANG": "C.UTF-8",
        "ANTHROPIC_BASE_URL": url,
        "ANTHROPIC_API_KEY": "pilot-router-ticket-not-a-key",
        "ANTHROPIC_CUSTOM_HEADERS": f"x-dittobench-step: {step}",
        "CLAUDE_CODE_AUTO_COMPACT_WINDOW": COMPACT_WINDOW,
        **({"CLAUDE_AUTOCOMPACT_PCT_OVERRIDE": COMPACT_PCT} if COMPACT_PCT else {}),
        "CLAUDE_CODE_DISABLE_EXPERIMENTAL_BETAS": "1",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "DISABLE_TELEMETRY": "1",
        "DISABLE_AUTOUPDATER": "1",
        "DISABLE_ERROR_REPORTING": "1",
        "ANTHROPIC_SMALL_FAST_MODEL": "claude-haiku-5-5",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": "claude-haiku-5-5",
    }
    argv = [
        "runuser",
        "-u",
        RUNNER,
        "--",
        "env",
        "-i",
        *[f"{k}={v}" for k, v in env.items()],
        "timeout",
        str(args.session_seconds),
        str(CLAUDE),
        "-p",
        prompt,
        "--output-format",
        "json",
        "--max-turns",
        str(args.max_turns),
        "--model",
        model,
        "--allowedTools",
        TOOLS,
    ]
    started = time.time()
    process = subprocess.Popen(
        argv, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True
    )
    stopped = {"reason": ""}

    def watch() -> None:
        while process.poll() is None:
            if spent(log) >= args.hard_usd:
                stopped["reason"] = f"budget: run spend reached ${args.hard_usd}"
                os.killpg(process.pid, signal.SIGTERM)
                return
            time.sleep(15)

    threading.Thread(target=watch, daemon=True).start()
    out, err = process.communicate()
    seconds = round(time.time() - started, 1)
    try:
        result = json.loads(out.strip().splitlines()[-1]) if out.strip() else {}
    except ValueError:
        result = {"raw": out[-2000:]}
    transcripts = sorted((home / ".claude" / "projects").glob("*/*.jsonl"))
    boundaries = 0
    for path in transcripts:
        for line in path.read_text(encoding="utf-8").splitlines():
            if '"compact_boundary"' in line:
                boundaries += 1
    rows = relay_rows(log, step)
    compactions = [r for r in rows if is_compaction(r)]
    errors = [
        f"{r.get('route_id')} {r.get('status')} {str(r.get('error'))[:120]}"
        for r in rows
        if int(r.get("status") or 0) >= 400
    ]
    return {
        "step": step,
        "exit": process.returncode,
        "stopped": stopped["reason"],
        "seconds": seconds,
        "result": result,
        "stderr": err[-1500:],
        "transcripts": [str(p) for p in transcripts],
        "compact_boundaries": boundaries,
        "requests": len(rows),
        "compaction_requests": len(compactions),
        "compaction_ok": sum(1 for r in compactions if int(r.get("status") or 0) < 400),
        "compaction_cost_usd": round(sum(int(r.get("cost_usd_micros") or 0) for r in compactions) / 1e6, 6),
        "cost_usd": round(sum(int(r.get("cost_usd_micros") or 0) for r in rows) / 1e6, 6),
        "relay_errors": errors,
    }


def smoke(args: argparse.Namespace, log: Path) -> bool:
    folder = WORKSPACES / "smoke"
    folder.mkdir(parents=True, exist_ok=True)
    must(f"chown -R {RUNNER}:{RUNNER} {folder}")
    ok = True
    for arm, url, _ in ARMS:
        found = session(
            "smoke",
            arm,
            url,
            args.model,
            folder,
            Path("/usr"),
            "Reply with just the word ok.",
            argparse.Namespace(**{**vars(args), "max_turns": 1}),
            log,
        )
        text = str(found["result"].get("result", "")).strip().lower()
        say(
            f"smoke {arm}: exit {found['exit']} result {text[:40]!r} requests {found['requests']} errors {found['relay_errors'][:2]} stderr {found['stderr'][-300:]!r}"
        )
        ok = ok and found["exit"] == 0 and "ok" in text
    return ok


def collect(task_id: str, arm: str, found: dict[str, Any], out: Path, workspace: Path) -> None:
    keep = out / "transcripts" / f"{task_id}-{arm}"
    keep.mkdir(parents=True, exist_ok=True)
    for path in found["transcripts"]:
        shutil.copy(path, keep / Path(path).name)
    (keep / "result.json").write_text(json.dumps(found["result"], indent=1))
    diff = sh("git add -A -N . && git diff", cwd=workspace).stdout
    (keep / "workspace.diff").write_text(diff)


def main() -> int:
    parser = argparse.ArgumentParser(prog="live-run")
    parser.add_argument("--tasks", default="/opt/pilot/live/tasks.json")
    parser.add_argument("--relay-log", default="/work/relay.jsonl")
    parser.add_argument("--out", default="/work/live")
    parser.add_argument("--model", default=os.environ.get("LIVE_MODEL", "claude-sonnet-5-5"))
    parser.add_argument("--fallback-model", default="claude-haiku-5-5")
    parser.add_argument("--count", type=int, default=int(os.environ.get("LIVE_TASKS", "3")))
    parser.add_argument("--cap-usd", type=float, default=float(os.environ.get("LIVE_CAP_USD", "13")))
    parser.add_argument("--hard-usd", type=float, default=float(os.environ.get("LIVE_HARD_USD", "14")))
    parser.add_argument("--switch-usd", type=float, default=float(os.environ.get("LIVE_SWITCH_USD", "4")))
    parser.add_argument("--max-turns", type=int, default=int(os.environ.get("LIVE_MAX_TURNS", "150")))
    parser.add_argument(
        "--session-seconds", type=int, default=int(os.environ.get("LIVE_SESSION_SECONDS", "3600"))
    )
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    log = Path(args.relay_log)
    sessions = out / "sessions.jsonl"
    setup()
    if not wait_for_card():
        say("the card never served; stopping")
        return 1
    if not smoke(args, log):
        say("the smoke run failed; stopping before any task")
        return 1
    skip = {s for s in os.environ.get("LIVE_SKIP", "").split(",") if s}
    tasks = [
        t for t in json.loads(Path(args.tasks).read_text(encoding="utf-8")) if t["instance_id"] not in skip
    ]
    model = args.model
    counted = 0
    first_cost: float | None = None
    largest = 2.5
    for order, task in enumerate(tasks, 1):
        if counted >= args.count:
            break
        if spent(log) + 2 * largest > args.cap_usd:
            say(f"stopping: ${spent(log):.2f} spent, the next task could pass the ${args.cap_usd} cap")
            break
        task_id = task["instance_id"]
        say(f"task {order} {task_id}")
        try:
            workspace, venv = prepare(task)
            gold = validate(task, workspace, venv)
        except (RuntimeError, subprocess.TimeoutExpired) as exc:
            say(f"  setup failed: {str(exc)[:600]}")
            gold = None
        if gold is None:
            with sessions.open("a") as handle:
                handle.write(
                    json.dumps({"task_id": task_id, "order": order, "skipped": "setup or validation failed"})
                    + "\n"
                )
            continue
        task_cost = 0.0
        compacted = True
        for arm, url, label in ARMS:
            if arm == "B" and not wait_for_card():
                say("  the card stopped serving; stopping")
                return 1
            reset(task, workspace)
            say(f"  arm {arm} ({label}) on {model}, ${spent(log):.2f} spent so far")
            found = session(
                task_id,
                arm,
                url,
                model,
                workspace,
                venv,
                PROMPT.format(issue=task["problem_statement"]),
                args,
                log,
            )
            collect(task_id, arm, found, out, workspace)
            graded = grade(task, workspace, venv, gold)
            row = {
                "task_id": task_id,
                "order": order,
                "arm": arm,
                "router": label,
                "model": model,
                **found,
                **graded,
            }
            row["result"] = {
                k: row["result"].get(k)
                for k in (
                    "subtype",
                    "is_error",
                    "num_turns",
                    "duration_ms",
                    "total_cost_usd",
                    "usage",
                    "result",
                )
                if isinstance(row["result"], dict)
            }
            with sessions.open("a") as handle:
                handle.write(json.dumps(row) + "\n")
            say(
                f"  arm {arm}: passed {graded['passed']}, compactions {found['compact_boundaries']} ({found['compaction_requests']} requests), cost ${found['cost_usd']:.3f}, {found['seconds']:.0f} s, turns {row['result'].get('num_turns')}, {found['stopped'] or 'finished'}"
            )
            task_cost += found["cost_usd"]
            largest = max(largest, found["cost_usd"])
            compacted = compacted and found["compact_boundaries"] > 0
            reset(task, workspace)
            if found["stopped"]:
                say("stopping: the budget guard ended the session")
                return 0
        if compacted:
            counted += 1
        else:
            say(f"  {task_id} did not compact in both arms; it does not count")
        if first_cost is None:
            first_cost = task_cost
            if task_cost > args.switch_usd and model != args.fallback_model:
                say(
                    f"  the first task cost ${task_cost:.2f}, over ${args.switch_usd}; switching to {args.fallback_model}"
                )
                model = args.fallback_model
    say(f"done: {counted} counted tasks, ${spent(log):.2f} spent")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
