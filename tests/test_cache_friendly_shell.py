"""DREAM-112 gate finding 3: `policy.shell_read_only` must be conservative. A contained command it calls a
read runs without asking in every mode but plan (fix #41), and a round of such commands costs half on a
local engine (fix #17). Every form below writes, runs or changes something, and was classified a read.
"""
from __future__ import annotations

import pytest

from dream.core import policy
from dream.core.execution import ExecutionScope, SandboxCapability

WRITES = [
    # output redirection, every form (the gate: >| <> >&), and the harmless-looking ones too
    "echo hi >| out.txt", "cat a <> b", "echo x >& out.txt", "echo x &> out.txt", "echo x>>out.txt",
    "ls missing 2>/dev/null", "cat f 2>&1",
    # substitution runs a command of its own, quoted or not
    "cat `rm x`", "cat <(rm x)", "cat >(rm x)", 'echo "$(rm x)"', "echo $(rm x)",
    # find actions
    "find . -delete", "find . -exec rm {} ;", "find . -execdir rm {} +", "find . -ok rm {} ;",
    "find . -fprint list.txt", "find . -fprintf out %p", "find . -fls out",
    # awk programs that write, pipe or run
    "awk '{print > \"out\"}' f", "awk '{print $1 >> \"out\"}' f", "awk '{print | \"sh\"}' f",
    "awk 'BEGIN { system(\"rm x\") }'", "awk -f prog.awk f", "awk -i inplace '{print}' f",
    # gawk's indirect call runs any function by name, system included (gate round 2)
    "awk 'BEGIN { f = \"sys\" \"tem\"; @f(\"touch x\") }'", "awk 'BEGIN { x = \"sys\"; y = x \"tem\"; @y(\"id\") }'",
    # sed scripts that write or run, or flags that are not reads
    "sed -n 'w out' f", "sed 's/a/b/w out' f", "sed -n '1p;w out' f", "sed '1e rm x' f", "sed -f script.sed f",
    "sed -i 's/a/b/' f", "sed -ni 's/a/b/p' f", "sed --in-place 's/a/b/' f",
    # git verbs and forms that change state or write files
    "git branch -D main", "git branch newbranch", "git branch -m a b", "git branch --set-upstream-to=o/m",
    "git remote add o https://example.com/r.git", "git remote remove o", "git remote set-url o u",
    "git remote prune o", "git diff --output=patch.diff", "git log --output=log.txt", "git diff --ext-diff",
    "git -c core.pager=sh log", "git commit -am x",
    # output files of read tools
    "sort -o out f", "sort -oout f", "sort --output=out f", "sort --compress-program=gzip f", "sort -T /tmp f",
    "uniq in out", "uniq -c in out", "tree -o out", "tree -R", "file -C -m magic",
    "date -s '2027-01-01'", "date --set=2027-01-01", "date 010100002027",
    "rg --pre=./run.sh x .", "less '+!rm x' f", "more -d f",
    # a mid-word # used to hide everything after it from the classifier (shlex took it for a comment)
    "echo a#b; rm -rf build", "ls # comment; rm x",
    # privilege is never a read
    "sudo cat /etc/shadow",
]

READS = [
    "ls -la", "cat f", "grep -rn foo .", "cd x && ls", 'cd "/p" && ls -la src | head -40',
    "git status", "git status --porcelain", "git log --oneline -5", "git diff HEAD~1 --stat", "git show HEAD:f",
    "git blame -L 1,10 f", "git branch", "git branch -a", "git branch -vv", "git branch --list 'feat*'",
    "git branch --contains HEAD", "git remote", "git remote -v", "git remote show origin", "git remote get-url origin",
    "sed -n '1,20p' f", "sed 's/a/b/g' f", "sed -n '/foo/,/bar/p' f", "sed '10q' f", "sed -n -e '5p' f",
    "sed -ne 's/x/y/p' f", "sed -E 's/(a)/b/g' f", "sed '/^#/d' f",
    "awk '{print $1}' f", "awk -F: '{print $1}' f", "awk 'NR>=10 && NR<=20 {print}' a.js",
    "awk '$3 > 100 {print $1}' f", "awk -v n=3 '{print $n}' f",
    "find . -name '*.py' -type f", "find . -maxdepth 2 -mtime -1 -print", "find src -newermt 2026-01-01 -o -empty",
    "sort -n f", "sort -k2,2 -t, f", "sort -rn f | head", "sort f | uniq -c", "uniq -c f", "uniq -w 5 f",
    "tree -L 2", "tree -a -I node_modules", "head -50 f | tail -10", "wc -l f", "echo hi", "jq .x f",
    "diff a b", "du -sh .", "date +%s", "date -u", "date -I", "date -Iseconds", "date -d yesterday +%F",
    "file x", "file --mime-type x", "rg -n pat .", "less f", "env FOO=1 cat f", "time cat f",
]


@pytest.mark.parametrize("command", WRITES)
def test_what_writes_is_not_a_read(command):
    assert policy.shell_read_only(command) is False


@pytest.mark.parametrize("command", READS)
def test_reads_stay_reads(command):
    assert policy.shell_read_only(command) is True


def _decision(workspace, command, mode="ask"):
    scope = ExecutionScope(workspace)
    capability = SandboxCapability(True, "fixture", scope, "/fixture/bwrap")
    return policy.decide("run_bash", {"command": command}, mode, workspace,
                         execution_scope=scope, execution_capability=capability)


@pytest.mark.parametrize("command", WRITES)
def test_a_contained_write_is_never_auto_allowed_as_a_read(tmp_path, command):
    decision, reason = _decision(tmp_path, command)
    assert decision != "allow" or reason != "read-only command inside workspace containment"
    assert decision in {"ask", "deny"}


def test_the_hidden_rm_now_reaches_the_consequential_check(tmp_path):
    """Before, `echo a#b; rm -rf build` showed every check only `echo a`: the rm ran unasked."""
    assert policy._shell_parts("echo a#b; rm -rf build") == [["echo", "a#b"], ["rm", "-rf", "build"]]
    assert _decision(tmp_path, "echo a#b; rm -rf build")[0] == "ask"
