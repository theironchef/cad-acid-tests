"""Run the acid tests in SOLIDWORKS, one worker process per test, with a per-stage time limit.

usage: python tools/run_solidworks.py [acid-1 acid-2 ...] [--limit 300] [--out results/x.jsonl]

Needs a running SOLIDWORKS with no document open, and a Python with pywin32 (this one: the workers are started with
the same interpreter). The probe and interference self-test (solidworks/acid.py --selftest) runs first. A stage that
is still running after --limit seconds is recorded as "hang"; the worker's whole process tree is killed and
SOLIDWORKS, which is still busy with the operation, is closed and started again. SOLIDWORKS is also restarted between
tests once its private memory passes --memory MB (default 4000). It is never closed while it holds a document the
workers did not open, and a dialog that blocks it stops the run: it is never clicked. Results are appended as JSON
lines; the default file is results/solidworks-<version>.jsonl. SOLIDWORKS redraws every feature while its window is
open, which makes the stage times several times longer (not the outcomes); with its window minimized the eleven tests
take a few minutes.
"""
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HARNESS = os.path.join(ROOT, "solidworks", "acid.py")
ALL = ["acid-%d" % i for i in range(1, 12)]
HARMLESS = {"tooltips_class32", "SysShadow", "#32768", "IME", "MSCTFIME UI", "swBalloonTip"}


class Stop(Exception):
    """The run cannot go on safely: reported, nothing is clicked or closed."""


def py(code, timeout):
    """Run a few lines of Python (pywin32) in a fresh process; None if it does not answer in time."""
    try:
        r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return None
    return r.stdout.strip() if r.returncode == 0 else None


def sw_processes():
    """[(pid, private MB)] of SLDWORKS.exe (WMI)."""
    import pythoncom
    import win32com.client
    pythoncom.CoInitialize()
    q = "SELECT ProcessId, PrivatePageCount FROM Win32_Process WHERE Name='SLDWORKS.exe'"
    return [(int(p.ProcessId), int(int(p.PrivatePageCount) / 2 ** 20))
            for p in win32com.client.GetObject("winmgmts:").ExecQuery(q)]


def dialogs(pid):
    """Visible top-level windows of SOLIDWORKS other than its main window: dialogs."""
    import win32gui
    import win32process
    found = []

    def cb(h, _):
        try:
            if win32process.GetWindowThreadProcessId(h)[1] == pid and win32gui.IsWindowVisible(h):
                c, t = win32gui.GetClassName(h), win32gui.GetWindowText(h)
                if c not in HARMLESS and (c == "#32770" or not t.startswith("SOLIDWORKS")):
                    found.append("%s %r" % (c, t))
        except Exception:
            pass
        return True
    win32gui.EnumWindows(cb, None)
    return found


DOCS = ("import pythoncom, win32com.client.dynamic\n"
        "o = pythoncom.GetActiveObject('SldWorks.Application')\n"
        "sw = win32com.client.dynamic.Dispatch(o.QueryInterface(pythoncom.IID_IDispatch))\n"
        "print(len(list(sw.GetDocuments or [])))\n"
        "for d in list(sw.GetDocuments or []): print(d.GetTitle)\n")


def open_docs(timeout=60):
    out = py(DOCS, timeout)
    if out is None:
        return None
    lines = out.splitlines()
    return lines[1:1 + int(lines[0])]


def one_sw():
    pr = sw_processes()
    if len(pr) != 1:
        raise Stop("expected one running SOLIDWORKS, found %d" % len(pr))
    if dialogs(pr[0][0]):
        raise Stop("SOLIDWORKS shows a dialog: %s" % dialogs(pr[0][0]))
    return pr[0]


def start_sw():
    """Start SOLIDWORKS (visible, left under the user's control) and wait until it answers with no dialog up."""
    code = ("import win32com.client\nsw = win32com.client.Dispatch('SldWorks.Application')\n"
            "sw.Visible = True\nsw.UserControl = True\nprint(sw.RevisionNumber)\n")
    if py(code, 600) is None:
        raise Stop("SOLIDWORKS did not start")
    time.sleep(20)
    one_sw()
    if open_docs() != []:
        raise Stop("SOLIDWORKS came back with documents open")


def restart_sw(pid, kill, mine):
    """Close SOLIDWORKS (kill: it is busy after a hang; else ExitApp) and start it again. Only when every document
    it holds is one the worker opened (mine); a SOLIDWORKS too busy to answer held none other when the test began."""
    docs = open_docs(30)
    if docs is not None and any(d not in mine for d in docs):
        raise Stop("SOLIDWORKS holds documents the workers did not open: %s" % docs)
    if kill:
        subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True, text=True)
    else:
        py("import win32com.client.dynamic, pythoncom\n"
           "o = pythoncom.GetActiveObject('SldWorks.Application')\n"
           "win32com.client.dynamic.Dispatch(o.QueryInterface(pythoncom.IID_IDispatch)).ExitApp()\n", 120)
    t0 = time.time()
    while sw_processes() and time.time() - t0 < 180:
        time.sleep(2)
    if sw_processes():
        raise Stop("SOLIDWORKS did not close")
    start_sw()


def read_progress(prog):
    try:
        lines = open(prog, encoding="utf-8").read().splitlines()
        stage, started = lines[0].split()
        return int(stage), float(started), json.loads(lines[1]) if len(lines) > 1 else []
    except (OSError, ValueError, IndexError):
        return None


def run_worker(args, env, log, limit, prog):
    """Run one worker: (None, its exit code) when it ends, ("hang", (stage, its documents)) when a stage outlives the
    limit; Stop when a SOLIDWORKS dialog blocks it for 30 s."""
    sw_pid = one_sw()[0]
    if os.path.exists(prog):
        os.remove(prog)
    p = subprocess.Popen([sys.executable, HARNESS] + args, env=env, stdout=log, stderr=subprocess.STDOUT)
    blocked = None
    while p.poll() is None:
        time.sleep(1)
        pg = read_progress(prog)
        dl = dialogs(sw_pid)
        blocked = (blocked or time.time()) if dl else None
        if blocked and time.time() - blocked > 30:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, text=True)
            raise Stop("a SOLIDWORKS dialog blocks the run (left open, not clicked): %s" % dl)
        if pg and time.time() - pg[1] > limit:
            subprocess.run(["taskkill", "/PID", str(p.pid), "/T", "/F"], capture_output=True, text=True)
            p.wait()
            return "hang", (pg[0], pg[2])
    return None, p.returncode


def last_record(out, test):
    last = None
    if os.path.exists(out):
        for line in open(out, encoding="utf-8"):
            r = json.loads(line)
            if r.get("test") == test:
                last = r
    return last


def main(argv):
    args = list(argv)
    limit, out, mem = 300, None, 4000
    for flag in ("--limit", "--out", "--memory"):
        if flag in args:
            i = args.index(flag)
            v = args[i + 1]
            del args[i:i + 2]
            if flag == "--limit":
                limit = int(v)
            elif flag == "--out":
                out = v
            else:
                mem = int(v)
    tests = args or ALL
    one_sw()
    docs = open_docs()
    if docs is None or docs:
        raise Stop("SOLIDWORKS must be running with no document open (open: %s)" % docs)
    version = subprocess.run([sys.executable, HARNESS, "--version"], capture_output=True, text=True,
                             timeout=300).stdout.strip().splitlines()[-1]
    m = re.match(r"SOLIDWORKS (\d+) SP(\d+\.\d+)$", version)
    if not m:
        raise Stop("unexpected version %r" % version)
    out = os.path.abspath(out or os.path.join(ROOT, "results", "solidworks-%s-sp%s.jsonl" % m.groups()))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    print(version, "->", out, flush=True)
    env = dict(os.environ, AT_OUT=out + ".selftest", AT_REPO=ROOT)
    with open(out + ".selftest.log", "w", encoding="utf-8") as log:
        what, code = run_worker(["--selftest"], env, log, limit, out + ".selftest.progress")
    if what or code:
        raise Stop("the probe and interference self-test failed (%s); see %s.selftest.log" % (what or code, out))
    print("self-test passed", flush=True)
    t_all = time.time()
    for t in tests:
        pid, mb = one_sw()
        if mb > mem:
            print("SOLIDWORKS uses %d MB: restarting it" % mb, flush=True)
            restart_sw(pid, False, [])
        if open_docs() != []:
            raise Stop("SOLIDWORKS holds documents before %s" % t)
        t0 = time.time()
        env = dict(os.environ, AT_TEST=t, AT_OUT=out, AT_REPO=ROOT)
        with open(out + "." + t + ".log", "w", encoding="utf-8") as log:
            what, info = run_worker([], env, log, limit, out + ".progress")
        if what == "hang":
            stage, mine = info
            with open(out, "a", encoding="utf-8") as f:
                f.write(json.dumps({"stage": stage, "outcome": "hang", "seconds": limit,
                                    "detail": "no result within %d s" % limit, "pipeline": "solidworks",
                                    "version": version, "test": t}) + "\n")
            print("%s stage %d hung: restarting SOLIDWORKS" % (t, stage), flush=True)
            restart_sw(one_sw()[0] if sw_processes() else 0, True, mine)
        else:
            pg = read_progress(out + ".progress")
            last = last_record(out, t)
            if pg and (last is None or last["stage"] < pg[0]):
                with open(out, "a", encoding="utf-8") as f:
                    f.write(json.dumps({"stage": pg[0], "outcome": "error", "seconds": round(time.time() - pg[1], 3),
                                        "detail": "the worker ended without a result (exit code %s)" % info,
                                        "pipeline": "solidworks", "version": version, "test": t}) + "\n")
        print("%s done in %.0f s" % (t, time.time() - t0), flush=True)
    for f in (out + ".progress", out + ".selftest.progress"):
        if os.path.exists(f):
            os.remove(f)
    print("all done in %.0f s; results: %s" % (time.time() - t_all, out), flush=True)


if __name__ == "__main__":
    try:
        main(sys.argv[1:])
    except Stop as e:
        print("STOP:", e, flush=True)
        sys.exit(2)
