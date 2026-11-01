"""Exercise docs publishing with real local git and a GitHub CLI boundary double."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
OLD = "20261019T171343Z"
NOW = "20261101T120000Z"
NEW = "20261102T120000Z"
DIGEST = "sha256:" + "a" * 64


def listing(tag, digest=DIGEST):
    return {"schema_version": 1, "generated_at": f"{tag[:4]}-{tag[4:6]}-{tag[6:8]}T{tag[9:11]}:{tag[11:13]}:{tag[13:15]}Z" if tag else None,
            "index_digest": digest if tag else None, "packs": []}


GH_DOUBLE = r'''#!/usr/bin/env python3
import json, os, pathlib, subprocess, sys
args=sys.argv[1:]; path=pathlib.Path(os.environ["GH_STATE"])
s=json.loads(path.read_text())
def opt(flag): return args[args.index(flag)+1]
if args[:2]==["api","user"]:
    print("publisher" if "--jq" in args else json.dumps({"login":"publisher"}))
elif args[:2]==["repo","view"]:
    print("main" if "--jq" in args else json.dumps({"defaultBranchRef":{"name":"main"}}))
elif args[:2]==["pr","list"]:
    rows=[p for p in s["prs"] if p.get("state","OPEN")=="OPEN"]
    if "--head" in args: rows=[p for p in rows if p["headRefName"]==opt("--head")]
    print(json.dumps(rows))
elif args[:2]==["pr","create"]:
    if s.get("fail_create"):
        s["fail_create"]=False;path.write_text(json.dumps(s));sys.exit("temporary GitHub create failure")
    branch=opt("--head"); oid=subprocess.check_output(["git","--git-dir",os.environ["GH_REMOTE"],"rev-parse",f"refs/heads/{branch}"],text=True).strip()
    p={"number":len(s["prs"])+1,"headRefName":branch,"headRefOid":oid,"baseRefName":opt("--base"),"isCrossRepository":False,"author":{"login":"publisher"},"state":"OPEN","url":"https://github.com/example/docs/pull/1"}
    s["prs"].append(p);path.write_text(json.dumps(s));print(p["url"])
elif args[:2]==["pr","close"]:
    if s.get("fail_close"):
        s["fail_close"]=False;path.write_text(json.dumps(s));sys.exit("temporary GitHub close failure")
    number=int(args[2]);s["closed"].append(number)
    for p in s["prs"]:
        if p["number"]==number:p["state"]="CLOSED"
    path.write_text(json.dumps(s))
else:sys.exit("unexpected gh invocation: "+repr(args))
'''


class PublishDocsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="catalog-docs-test-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.remote = self.root / "remote.git"
        self.seed = self.root / "seed"
        self.git("init", "--bare", str(self.remote))
        self.git("init", "-b", "main", str(self.seed))
        self.git("config", "user.name", "Maintainer", cwd=self.seed)
        self.git("config", "user.email", "maintainer@example.com", cwd=self.seed)
        (self.seed / "src/data").mkdir(parents=True)
        (self.seed / "src/data/marquee.json").write_text(json.dumps(listing(None)))
        (self.seed / "README.md").write_text("docs\n")
        self.git("add", ".", cwd=self.seed)
        self.git("commit", "-m", "docs baseline", cwd=self.seed)
        self.git("remote", "add", "origin", str(self.remote), cwd=self.seed)
        self.git("push", "origin", "main", cwd=self.seed)
        self.git("symbolic-ref", "HEAD", "refs/heads/main", cwd=self.remote)
        self.state = self.root / "github.json"
        self.state.write_text(json.dumps({"prs": [], "closed": []}))
        self.bin = self.root / "bin"
        self.bin.mkdir()
        (self.bin / "gh").write_text(GH_DOUBLE)
        (self.bin / "gh").chmod(0o755)

    def git(self, *args, cwd=None):
        return subprocess.check_output(["git", *args], cwd=cwd or self.root,
                                       stderr=subprocess.DEVNULL, text=True).strip()

    def read_state(self):
        return json.loads(self.state.read_text())

    def update_state(self, **fields):
        self.state.write_text(json.dumps({**self.read_state(), **fields}))

    def branch(self, tag=NOW, *, content=None, extra=False, own=True, symlink=False):
        name = "marquee/" + tag
        self.git("checkout", "-b", name, "main", cwd=self.seed)
        (self.seed / "src/data/marquee.json").write_text(json.dumps(content or listing(tag)))
        if symlink:
            (self.seed / "src/data/marquee.json").unlink()
            (self.seed / "src/data/marquee.json").symlink_to("../../README.md")
        if extra:
            (self.seed / "README.md").write_text("unexpected change\n")
        self.git("add", ".", cwd=self.seed)
        self.git("-c", f"user.name={'github-actions[bot]' if own else 'Other'}", "-c",
                 f"user.email={'41898282+github-actions[bot]@users.noreply.github.com' if own else 'other@example.com'}",
                 "commit", "-m", f"Marquee: catalog {tag}", cwd=self.seed)
        oid = self.git("rev-parse", "HEAD", cwd=self.seed)
        self.git("push", "origin", name, cwd=self.seed)
        self.git("checkout", "main", cwd=self.seed)
        return name, oid

    def pr(self, tag, *, own=True, cross=False, number=10):
        name, oid = self.branch(tag)
        row = {"number": number, "headRefName": name, "headRefOid": oid,
               "baseRefName": "main", "isCrossRepository": cross,
               "author": {"login": "publisher" if own else "intruder"}, "state": "OPEN"}
        self.update_state(prs=self.read_state()["prs"] + [row])

    def run_publish(self, tag=NOW):
        work = Path(tempfile.mkdtemp(dir=self.root, prefix="attempt-"))
        docs = work / "stagehand-docs"
        self.git("clone", str(self.remote), str(docs))
        (work / "marquee.json").write_text(json.dumps(listing(tag)))
        env = {**os.environ, "PATH": f"{self.bin}{os.pathsep}{os.environ['PATH']}",
               "GH_STATE": str(self.state), "GH_REMOTE": str(self.remote), "TAG": tag,
               "GENERATED_AT": listing(tag)["generated_at"], "INDEX_DIGEST": DIGEST,
               "INDEX_REF": "oci://ghcr.io/example/catalog", "DOCS_REPO": "example/docs",
               "GH_TOKEN": "test-boundary-token", "RUN_URL": "https://github.com/example/catalog/actions/runs/1",
               "GITHUB_STEP_SUMMARY": str(work / "summary")}
        command = [sys.executable, str(ROOT / "scripts/publish-docs.py"), str(docs), str(work / "marquee.json")]
        return subprocess.run(command, cwd=work, env=env, text=True, capture_output=True)

    def test_retry_recovers_exact_pushed_branch_after_pr_creation_failed(self):
        self.update_state(fail_create=True)
        first = self.run_publish()
        self.assertNotEqual(first.returncode, 0, first.stdout)
        before = self.git("rev-parse", f"refs/heads/marquee/{NOW}", cwd=self.remote)
        second = self.run_publish()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.read_state()["prs"][0]["headRefName"], "marquee/" + NOW)
        self.assertEqual(self.git("rev-parse", f"refs/heads/marquee/{NOW}", cwd=self.remote), before)

    def test_older_retry_does_not_close_or_publish_over_newer_owned_pr(self):
        self.pr(NEW)
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()["closed"], [])
        self.assertEqual(len(self.read_state()["prs"]), 1)
        self.assertIn("newer", result.stdout.lower())

    def test_retry_finishes_older_pr_cleanup_after_creation_succeeded(self):
        self.pr(OLD)
        self.update_state(fail_close=True)
        first = self.run_publish()
        self.assertNotEqual(first.returncode, 0, first.stdout)
        self.assertEqual(len(self.read_state()["prs"]), 2)
        before = self.git("rev-parse", f"refs/heads/marquee/{NOW}", cwd=self.remote)
        second = self.run_publish()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.read_state()["closed"], [10])
        self.assertEqual(len(self.read_state()["prs"]), 2)
        self.assertEqual(self.git("rev-parse", f"refs/heads/marquee/{NOW}", cwd=self.remote), before)

    def test_older_retry_does_not_propose_rollback_of_merged_listing(self):
        (self.seed / "src/data/marquee.json").write_text(json.dumps(listing(NEW)))
        self.git("add", ".", cwd=self.seed)
        self.git("commit", "-m", "merge newer listing", cwd=self.seed)
        self.git("push", "origin", "main", cwd=self.seed)
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()["prs"], [])

    def test_existing_branch_with_extra_file_is_refused(self):
        _, before = self.branch(extra=True)
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state()["prs"], [])
        self.assertEqual(self.git("rev-parse", f"refs/heads/marquee/{NOW}", cwd=self.remote), before)

    def test_existing_branch_with_wrong_generated_content_is_refused(self):
        self.branch(content=listing(NOW, "sha256:" + "b" * 64))
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state()["prs"], [])

    def test_existing_branch_with_foreign_commit_identity_is_refused(self):
        self.branch(own=False)
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("provenance", result.stderr)
        self.assertEqual(self.read_state()["prs"], [])

    def test_existing_branch_with_symlink_listing_is_refused(self):
        self.branch(symlink=True)
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("regular file", result.stderr)
        self.assertEqual(self.read_state()["prs"], [])

    def test_foreign_pr_with_predictable_branch_is_refused(self):
        self.pr(NOW, own=False)
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.read_state()["closed"], [])

    def test_cross_repository_pr_with_predictable_branch_is_refused(self):
        self.pr(NOW, cross=True)
        result = self.run_publish()
        self.assertNotEqual(result.returncode, 0)

    def test_new_publish_closes_only_older_owned_timestamp_prs(self):
        self.pr(OLD)
        self.update_state(prs=self.read_state()["prs"] + [
            {"number": 20, "headRefName": "marquee/notes", "isCrossRepository": False, "author": {"login": "publisher"}},
            {"number": 21, "headRefName": "marquee/20261018T120000Z", "isCrossRepository": False, "author": {"login": "intruder"}},
        ])
        result = self.run_publish()
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.read_state()["closed"], [10])


if __name__ == "__main__":
    unittest.main()
