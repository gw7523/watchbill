from watchbill.classify import Allowlist, allow_relaunch, classify


def pane(**kw):
    return {"pane_id": "w1:p1", "tab_id": "w1:t1", "workspace_id": "w1", **kw}


def pi(*argvs):
    return {"foreground_processes": [{"argv": list(a), "cmdline": " ".join(a), "cwd": "/x", "name": a[0], "pid": i + 1}
                                     for i, a in enumerate(argvs)]}


def test_agent_detected_by_herdr_outranks_argv():
    c = classify(pane(agent="claude", agent_status="idle"), pi(["claude", "--dangerously-skip-permissions"]))
    assert (c.role, c.kind) == ("agent", "claude")


def test_agent_from_argv_when_herdr_has_not_detected():
    assert classify(pane(), pi(["codex"])).kind == "codex"
    assert classify(pane(), pi(["cursor-agent"])).kind == "cursor"
    assert classify(pane(), pi(["/usr/bin/grok"])).kind == "grok"


def test_bridge_remote_and_session_and_server():
    for argv in (["herdr", "--remote", "ser6"], ["herdr", "--session", "x"], ["herdr", "session", "attach", "x"], ["herdr", "server"]):
        assert classify(pane(), pi(argv)).role == "bridge", argv


def test_bridge_outranks_agent_detection():
    # a `herdr --remote` viewport can show a remote agent; the local pane is still a bridge
    assert classify(pane(agent="claude"), pi(["herdr", "--remote", "ser6"])).role == "bridge"


def test_watcher():
    assert classify(pane(), pi(["watchexec", "-w", "src", "--", "pytest"])).role == "watcher"
    assert classify(pane(), pi(["cargo", "watch", "-x", "test"])).role == "watcher"
    assert classify(pane(), pi(["entr"])).role == "watcher"


def test_poller():
    assert classify(pane(), pi(["tail", "-f", "x.log"])).role == "poller"
    assert classify(pane(), pi(["watch", "-n5", "df"])).role == "poller"
    assert classify(pane(), pi(["tail", "x.log"])).role == "shell"  # not following → not a poller


def test_server():
    assert classify(pane(), pi(["npm", "run", "dev"])).role == "server"
    assert classify(pane(), pi(["python3", "-m", "http.server"])).role == "server"
    assert classify(pane(), pi(["uvicorn", "app:app"])).role == "server"


def test_editor_and_shell():
    assert classify(pane(), pi(["nvim", "x"])).role == "editor"
    assert classify(pane(), None).role == "shell"
    assert classify(pane(), {"foreground_processes": []}).role == "shell"
    assert classify(pane(), pi(["some-unknown-tool"])).role == "shell"


def test_allowlist_and_relaunch():
    al = Allowlist.parse("# trusted\nwatchexec *\n\nnpm run dev\n")
    assert al.allows("watchexec -w src -- pytest")
    assert not al.allows("entr -r make")
    assert allow_relaunch("watcher", "watchexec -w src", al)
    assert not allow_relaunch("watcher", "entr -r make", al)
    assert not allow_relaunch("bridge", "herdr --remote x", al)
    assert allow_relaunch("agent", "claude", al)          # via native resume
    assert not allow_relaunch("shell", "", al)
    assert not allow_relaunch("watcher", "watchexec x", None)
