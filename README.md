# Stagehand Marquee catalog

This repository is the source of truth for **Stagehand Marquee**, the official
feed of Expansion Packs for Stagehand consoles. A reviewed change to
`catalog.yaml` is the only way a pack reaches the feed. When such a change is
merged, a workflow in this repository copies the reviewed image, signs it,
publishes a signed index, and opens a pull request that refreshes the public
Marquee page at <https://www.puppet-stagehand.com/marquee/>.

## What this is, for a ten year old

Think of a school that hands out approved games.

- A **pack** is a game in a sealed box (a container image).
- The **index** is the list on the notice board: "these games, these versions,
  and each box has this exact fingerprint." The fingerprint is called a digest.
  If anyone changes one byte in the box, the fingerprint changes.
- The **signature** is the head teacher's stamp on the list. Every Stagehand
  console owns a picture of the real stamp (the public key in this repository)
  and refuses any list that does not carry it.
- This repository is the head teacher's office. People propose a game by
  opening a pull request. Someone reads it. Only after it is merged does the
  stamp come out, and only the office holds the stamp.

## Add Marquee to a console

In a Stagehand console, add this address as an expansion source:

```
oci://ghcr.io/puppet-stagehand/catalog
```

The console reads the tag `latest`, checks the signature against the publisher
key below, and then offers the packs it lists. Nothing is trusted that is not
signed by that key. Website: <https://www.puppet-stagehand.com> (the public
Marquee page is at `/marquee/`).

## Publisher key

The public key is committed here as `marquee-publisher-key.pub.pem`. Its
fingerprint (sha256 of the DER-encoded public key) is:

```
sha256:48ba24e6f91da7ced7540b1512d0eac37b7fa070d474d28b9f8a89a0c5955242
```

Check it yourself:

```bash
MARQUEE_KEY_FINGERPRINT=sha256:48ba24e6f91da7ced7540b1512d0eac37b7fa070d474d28b9f8a89a0c5955242 \
  bash scripts/check-key.sh
```

The same fingerprint is embedded in the console and shown on the Marquee page.
This is an interim key. Its private half exists only in the password manager
of the people who run the feed and in the protected `marquee-publish` GitHub
environment of this repository; it is never committed and never written to a
runner disk.

## The feed format (for third parties)

If you run your own feed, you can use exactly the same format and tool.

The feed is an OCI artifact. Its single layer is a JSON document that follows
[`schema/index.schema.json`](schema/index.schema.json) (format 1, "App Center
index"). That file is a byte-for-byte copy of the schema in the public SDK at
the commit named in [`schema/SOURCE`](schema/SOURCE); the `validate` workflow
fails if the two ever differ.

Key facts a console relies on:

- `generated_at` is always stamped and **strictly increases** on every publish.
  A console refuses an older index than the newest it has seen, so nobody can
  replay an old list to bring back a removed pack.
- Every pack version names its image by digest (`...@sha256:<64 hex>`). Tags
  are never accepted.
- Each published index lives under an **immutable tag** shaped like
  `20261009T143012Z` (the same moment as `generated_at`; a colon is not legal in
  an image tag). The mutable tag `latest` moves only after the index is signed
  and has been verified from a machine with no credentials.
- The signature is a cosign "legacy" signature: a `sha256-<hex>.sig` tag next to
  the signed digest, by an ECDSA P-256 key. Images are signed the same way.

The tool that builds, checks, publishes and verifies such a feed is
`expansion-index` in the public SDK
([`cmd/expansion-index`](https://github.com/puppet-stagehand/stagehand-sdk/tree/main/cmd/expansion-index),
operator guide `docs/expansion-index.md`). It needs no credential to build or
verify a public feed.

### List a pack or pull a version

`catalog.yaml` lists each pack's `id`, `feed`, and its `versions`. A version has
a `version`, the `candidate` image **pinned by digest**, and a `released_at`
date. A pack's name, publisher, tier, licence, summary and permissions are
deliberately **not** typed here: the tool reads them from the manifest inside
the image, so the list can never disagree with the box.

To **list** a version: open a pull request that adds the entry. The `validate`
workflow checks it without any secret. After review and merge the publish
workflow does the rest.

To **pull** a version: open a pull request that removes the entry, review and
merge it. The publish workflow then publishes a newer index without that
version. This stops *new* installs only. It does not uninstall the version from
consoles that already have it, and the image itself stays in the registry
(delete it separately if it must not be pullable). Revoking a single installed
version is not designed yet.

## How a publish works

`.github/workflows/validate.yml` runs on every pull request and on pushes to
`main`. It has read-only permissions, no environment and no secrets. It builds
`expansion-index` from the public SDK at a pinned commit and runs
`expansion-index build --check`, the key fingerprint check, and the schema
byte comparison.

`.github/workflows/publish.yml` runs only on a push to `main` that changes
`catalog.yaml`, or on manual dispatch. It has three jobs:

1. `tools` builds `expansion-index` with no environment and no secrets, and
   records its sha256.
2. `publish` (environment `marquee-publish`, concurrency group
   `marquee-publish`, never cancelled mid-run, only on `refs/heads/main` and
   never on a pull request) refuses a tool whose checksum differs, then:
   validates every candidate by running `build` (reading the previous index
   with the job token); for each image the build says is not yet listed, copies
   the candidate by digest to `ghcr.io/puppet-stagehand/packs/<id>:<version>`
   and signs that digest; pushes the index under its immutable tag; signs the
   index digest; verifies it anonymously (with `--images`); only then promotes
   `latest`; and verifies `latest` anonymously once more.
3. `docs` (environment `marquee-docs`, which holds only the token that may open
   a pull request in the docs repository) turns the index that was just
   verified into the Marquee page data and opens or updates one pull request
   in `puppetlabs/stagehand-docs` that a person merges. The signing key is never
   in this job and the docs token is never in the signing job.

Signing flags (consoles verify the older legacy format, so these are required):
`cosign sign --key env://COSIGN_PRIVATE_KEY --new-bundle-format=false
--use-signing-config=false --tlog-upload=false --registry-referrers-mode=legacy
--yes`, with cosign pinned to v3.1.3.

## First publish of the feed or of a new pack id

GitHub Container Registry (GHCR) creates every new package **private**, and a
package can only be made public from the web page. So the first publish of the
feed, and the first publish of every new pack id (a new pack is a new package),
is expected to stop part way and be repeated once:

1. Merge the change. The first run copies the candidate and creates
   `ghcr.io/puppet-stagehand/packs/<id>` and (the first time ever)
   `ghcr.io/puppet-stagehand/catalog` as **private** packages, signs them, and
   then **stops at the anonymous verify**, because an anonymous pull of a
   private package is refused. `latest` is not moved. This is the expected
   outcome. The build step summary shows `previous: not_found`.
2. Make each new package public: GitHub organisation `puppet-stagehand` ->
   Packages -> the package -> Package settings -> Change visibility -> Public
   (this cannot be undone). In the same page, under **Manage Actions access**,
   check that `puppet-stagehand/catalog` has **Write**; without it later runs
   are refused on a package that is not linked to this repository.
3. Re-run the workflow (`gh workflow run publish.yml --repo
   puppet-stagehand/catalog --ref main`). The previous read still says not
   found (there is no `latest` yet), `copy` writes nothing new (the digest is
   already present), signing the same digest again adds a second signature layer
   by the same key (consoles check each layer, so it still verifies), and the
   anonymous verify, the promote and the docs job now succeed.
4. From the first promoted index on, `build` finds the previous index, and
   images already listed are never copied or signed again.

If the build step fails with a `previous_denied` finding, **do not work around
it**. A denied read of the previous index (an HTTP 401 or 403 on an existing
feed) is always fatal on purpose: treating it as "nothing there" would let a
failed read reset the rollback guard. No flag or variable relaxes it. Report
it, fix the credentials or the package's Actions access, and re-run.

## Before the production release (D-24)

Hardening is deliberately deferred until just before the production release. It
is listed here so it is not forgotten, and it is also an item on the release
gate checklist. Nothing here has been weakened; these are the things still to
tighten:

- [ ] **Required reviewers** on the `marquee-publish` environment, and on
      `marquee-docs`, so a person approves each run that releases the signing
      key or the docs token.
- [ ] **Main-only deployment branches**: restrict `marquee-publish` and
      `marquee-docs` to the `main` branch.
- [ ] **Admin-bypass review**: decide whether repository admins may bypass the
      environment's protection rules, and turn bypass off if not.
- [ ] **Protected `main`** on this repository, requiring a pull request review
      and the `validate` check before merge.
- [ ] **CODEOWNERS on `.github/workflows`** so a workflow change needs an owner
      review.
- [ ] **GHCR package visibility and Actions-access review**: confirm `catalog`
      and `packs/*` are public as intended, that only this repository has Write
      under Manage Actions access, and that no stale package is linked.
- [ ] **Staging token review**: `STAGING_REGISTRY_TOKEN` is a read-only
      (`read:packages`) token authorised for the `puppetlabs` organisation;
      confirm its owner, expiry and that it is only an environment secret of
      `marquee-publish`.
- [ ] **Docs token review**: `DOCS_PR_TOKEN` is a fine-grained token for the docs
      repository only (Contents and Pull requests), authorised for the
      `puppetlabs` organisation (which enforces SAML single sign-on); confirm
      its owner and expiry.
- [ ] **Interim key replacement**: replace the interim Marquee signing key with
      the production key held in the production key custody (this changes the
      embedded console key and this repository's PEM together).

## Out of scope here

The paid (private) feed, its registry credentials and runner, and retiring
other signing paths so that this workflow is the only signer, are separate,
later work.

## Repository map

| Path | What it is |
|------|------------|
| `catalog.yaml` | The reviewed list of what is published. |
| `marquee-publisher-key.pub.pem` | The public key consoles trust. |
| `schema/index.schema.json`, `schema/SOURCE` | Pinned copy of the index schema and the SDK commit it came from. |
| `scripts/check-key.sh` | Prints and checks the key fingerprint. |
| `.github/workflows/validate.yml` | Secret-free validation of every change. |
| `.github/workflows/publish.yml` | The environment-gated sign and publish workflow. |
