#!/usr/bin/env python3
"""Open a docs PR for verified listing data without rolling back a newer run.

Only the GitHub CLI talks to GitHub; git credentials remain in its credential
helper. A retry can reuse an exact, single-file generated commit, never rewrite
an existing branch. The caller verifies the signed index before invoking this.
"""
import datetime as dt
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile

DATA = "src/data/marquee.json"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"
TAG_PATTERN = re.compile(r"[0-9]{8}T[0-9]{6}Z")
AUTH = ["-c", "credential.helper=", "-c", "credential.helper=!gh auth git-credential"]


def run(*args):
    return subprocess.check_output(args, text=True).strip()


def tag_time(tag):
    if not TAG_PATTERN.fullmatch(tag):
        raise ValueError("expected an immutable YYYYMMDDTHHMMSSZ index tag")
    return dt.datetime.strptime(tag, "%Y%m%dT%H%M%SZ").replace(tzinfo=dt.timezone.utc)


def generation(data):
    value = data.get("generated_at")
    if value is None:
        if data.get("index_digest") is not None:
            raise ValueError("listing without generated_at has an index_digest")
        return None
    return dt.datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=dt.timezone.utc)


def publish(docs, listing):
    candidate_bytes = listing.read_bytes()
    candidate = json.loads(candidate_bytes)
    tag = os.environ["TAG"]
    when = tag_time(tag)
    digest = os.environ["INDEX_DIGEST"]
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", digest):
        raise ValueError("publish job did not return an index digest")
    if generation(candidate) != when or candidate["generated_at"] != os.environ["GENERATED_AT"]:
        raise ValueError("listing generated_at does not match the published immutable tag")
    if candidate.get("index_digest") != digest:
        raise ValueError("listing digest does not match the published index")
    repo = os.environ["DOCS_REPO"]
    branch = f"marquee/{tag}"

    def git(*args):
        return run("git", "-C", str(docs), *args)

    def gh(*args):
        return run("gh", *args)

    if git("status", "--porcelain"):
        raise ValueError("docs checkout is dirty; refusing to replace files")
    me = gh("api", "user", "--jq", ".login")
    base = gh("repo", "view", repo, "--json", "defaultBranchRef", "--jq", ".defaultBranchRef.name")
    git("check-ref-format", f"refs/heads/{base}")
    if git("rev-parse", "--is-shallow-repository") == "true":
        git(*AUTH, "fetch", "--no-tags", "--unshallow", "origin")
    git(*AUTH, "fetch", "--no-tags", "origin", f"refs/heads/{base}:refs/remotes/origin/{base}")
    base_sha = git("rev-parse", f"refs/remotes/origin/{base}")
    current_bytes = subprocess.check_output(["git", "-C", str(docs), "show", f"{base_sha}:{DATA}"])
    current = json.loads(current_bytes)
    current_when = generation(current)
    if current_when is not None and current_when > when:
        print("A newer listing is already merged; skipping stale docs run")
        return
    if current_when == when:
        if current != candidate:
            raise ValueError("same generation is already merged with different listing data")
        print("Docs main already matches the published index; no pull request needed")
        return

    prs = json.loads(gh("pr", "list", "--repo", repo, "--state", "open", "--limit", "1000",
                        "--json", "number,headRefName,headRefOid,baseRefName,isCrossRepository,author,url"))

    def owned(pr):
        return pr.get("isCrossRepository") is False and pr.get("author", {}).get("login") == me

    existing = [pr for pr in prs if pr.get("headRefName") == branch]
    if any(not owned(pr) or pr.get("baseRefName") != base for pr in existing):
        raise ValueError("predictable docs branch has a foreign pull request or unexpected base")

    def branch_commit(name):
        result = git(*AUTH, "ls-remote", "--heads", "origin", f"refs/heads/{name}")
        if not result:
            return None
        rows = result.splitlines()
        if len(rows) != 1 or rows[0].split()[1] != f"refs/heads/{name}":
            raise ValueError("ambiguous remote branch lookup")
        sha = rows[0].split()[0]
        git(*AUTH, "fetch", "--no-tags", "origin", f"refs/heads/{name}")
        if git("rev-parse", "FETCH_HEAD") != sha:
            raise ValueError("remote branch changed while being checked")
        return sha

    def check_generated_commit(sha, expected_tag, expected=None):
        parents = git("rev-list", "--parents", "-n", "1", sha).split()
        if len(parents) != 2:
            raise ValueError("generated docs branch is not a single-parent commit")
        parent = parents[1]
        git("merge-base", "--is-ancestor", parent, base_sha)
        if git("diff-tree", "--no-commit-id", "--name-only", "-r", sha).splitlines() != [DATA]:
            raise ValueError("existing generated branch changes unexpected files")
        if git("show", "-s", "--format=%ae%n%ce%n%s", sha).splitlines() != [
            BOT_EMAIL, BOT_EMAIL, f"Marquee: catalog {expected_tag}"
        ]:
            raise ValueError("existing docs branch lacks expected generated commit provenance")
        tree = git("ls-tree", sha, "--", DATA).split()
        if tree[:2] != ["100644", "blob"]:
            raise ValueError("generated listing is not a regular file")
        raw = subprocess.check_output(["git", "-C", str(docs), "show", f"{sha}:{DATA}"])
        data = json.loads(raw)
        if generation(data) != tag_time(expected_tag):
            raise ValueError("existing branch generation does not match its immutable tag")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", data.get("index_digest", "")):
            raise ValueError("existing branch lacks an index digest")
        if expected is not None and raw != expected:
            raise ValueError("existing docs branch differs from the exact verified listing")

    older = []
    for pr in prs:
        name = pr.get("headRefName", "")
        if not owned(pr) or pr.get("baseRefName") != base or not name.startswith("marquee/"):
            continue
        pr_tag = name[len("marquee/"):]
        if not TAG_PATTERN.fullmatch(pr_tag):
            continue
        tag_time(pr_tag)  # Reject calendar-invalid timestamp lookalikes.
        sha = branch_commit(name)
        if sha is None or sha != pr.get("headRefOid"):
            raise ValueError("owned docs PR head differs from its remote branch")
        check_generated_commit(sha, pr_tag, candidate_bytes if name == branch else None)
        if pr_tag > tag:
            print("A newer owned docs pull request exists; skipping stale docs run")
            return
        if pr_tag < tag:
            older.append(pr)

    def close_older(new_url):
        for pr in older:
            gh("pr", "close", str(pr["number"]), "--repo", repo, "--delete-branch",
               "--comment", f"Superseded by {new_url}")

    if existing:
        print(f"Our pull request for {branch} is already open; finishing older PR cleanup")
        close_older(existing[0]["url"])
        return
    sha = branch_commit(branch)
    if sha is not None:
        check_generated_commit(sha, tag, candidate_bytes)
        print(f"Recovering exact generated branch {branch} without rewriting it")
    else:
        if git("ls-tree", base_sha, "--", DATA).split()[:2] != ["100644", "blob"]:
            raise ValueError("docs main listing is not a regular file")
        git("checkout", "--detach", base_sha)
        git("checkout", "-b", branch)
        (docs / DATA).write_bytes(candidate_bytes)
        git("add", "--", DATA)
        git("-c", "user.name=github-actions[bot]", "-c", f"user.email={BOT_EMAIL}",
            "commit", "-m", f"Marquee: catalog {tag}")
        git(*AUTH, "push", "origin", f"HEAD:refs/heads/{branch}")

    body = ("Generated by the Stagehand Marquee catalog publish workflow.\n\n"
            f"- Index: {os.environ['INDEX_REF'].removeprefix('oci://')}@{digest}\n"
            f"- Immutable tag: {tag}\n- generated_at: {candidate['generated_at']}\n"
            f"- Workflow run: {os.environ['RUN_URL']}\n\n"
            "This replaces src/data/marquee.json with exactly what the signed, anonymously verified index lists. A person reviews and merges it.\n")
    with tempfile.TemporaryDirectory(prefix="marquee-docs-pr-") as directory:
        body_file = Path(directory) / "body.md"
        body_file.write_text(body)
        new_url = gh("pr", "create", "--repo", repo, "--base", base, "--head", branch,
                     "--title", f"Marquee: catalog {tag}", "--body-file", str(body_file))
    print(f"opened {new_url}")
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as summary:
        summary.write(f"docs pull request: {new_url}\n")
    close_older(new_url)


if __name__ == "__main__":
    try:
        if len(sys.argv) != 3:
            raise ValueError("usage: publish-docs.py DOCS_CHECKOUT VERIFIED_LISTING")
        publish(Path(sys.argv[1]).resolve(), Path(sys.argv[2]).resolve())
    except (ValueError, KeyError, subprocess.CalledProcessError) as error:
        sys.exit(f"Refusing docs publication: {error}")
