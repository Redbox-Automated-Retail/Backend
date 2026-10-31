# Installer seed zips (`data-<MMDDYYYY>.zip`)

A seed is the data file the store installer puts on a new kiosk. It holds every repository of the kiosk's
update store (`REDS\Update Manager\.store\.repository`) with nothing staged or activated yet. The installer's
"Activate Image" step then calls `repositoryupdate` and `repositoryactivate` for every repository
(`software/docs/systems/update-manager.md` §5.4, §5.5, §7.4). Each file comes from its seed, and updatemgrd
continues from those heads against the Update Service.

The backend reads seeds (`admin import-store`, the dashboard's **Update packages** page). It also writes them:
`admin build-seed`, or **Build a seed zip** on the same page. This document describes the format as found in
the 24 seeds from 2014-2024 in the archive, and as the builder writes it.

## Layout

```
<repo>/.active                                  "0000000000000000000000000000000000000000"
<repo>/.current                                 "0000000000000000000000000000000000000000"
<repo>/<RevHash>-<UPPERHEX(SHA1(ASCII(TargetPath + "\" + TargetName)))>.patch
<repo>/.revlog/0 ... .revlog/<n-1>
```

The zip has no top folder; the installer unpacks it into `.store\.repository`.

- **Repositories:** one folder each, named as the Update Service names them (`kop-v2-engine`, `dvd-data`, ...).
- **`.active`, `.current`:** JSON strings, 42 bytes, no newline. 40 zeros means nothing is activated or staged
  yet. updatemgrd writes the staged head into `.current` (§5.4), the activated head into `.active`, and its label
  into `.label` (§5.5). A seed has no `.label`.
- **`.revlog/<k>`:** one revision per file; `0` is the oldest, and the last one is the repository's head.
- **`*.patch`:** content files. The name is the revision hash, a dash, then the SHA-1 of the target path. The
  path string is hashed byte for byte, so case matters (`redbox` and `Redbox` give different names).

### Entry order and attributes

The real seeds and the builder use the same order and attributes:

- repositories are sorted by name;
- within a repository: `.active`, `.current`, the `.patch` files sorted by name, then the `.revlog` entries
  sorted **as strings** (`0, 1, 10, 100, 101, ..., 11, ...`);
- every entry is deflated, with no data descriptor (`flag_bits` 0);
- version made by 4.5 (DOS, `create_system` 0), version needed 2.0, `external_attr` 0x20 (archive);
- no extra fields and no zip comment;
- every entry carries the build time. Zip64 is used only for entries over 2 GB.

### Revlog entry

Compact JSON (no spaces) with the keys in this order; Python's `json.dumps(e, separators=(",", ":"))` reproduces
all 1,563 entries of `data-572024.zip` byte for byte:

```json
{"Hash":"654EECD2239C9616B300B810995F83E12D3FA7E4","Label":"kop-v2-engine-Calvin","Changes":[
 {"VersionHash":"0BDC...","ContentHash":"0914...","TargetName":"kioskengine.exe",
  "TargetPath":"C:\\Program Files\\Redbox\\REDS\\Kiosk Engine\\bin","IsSeed":true,"Composite":false}]}
```

| Field | Meaning |
|---|---|
| `Hash` | revision id, 40 upper-case hex. The server picks it; nothing on the kiosk recomputes it. A revlog may repeat a hash (`kop-shared-bundles` has one three times in a row); the changes are then on the first copy and the others are empty |
| `Label` | release label or `null` |
| `VersionHash` | SHA-1 of the file after this revision |
| `ContentHash` | informational; updatemgrd does not check it. Redbox's seeds keep the value from the revision as it was published, so for a re-seeded file it is often not the SHA-1 of the content file. The builder does the same, and uses `VersionHash` for new seeds |
| `IsSeed` | the content file is the whole file |
| `Composite` | placeholder: listed, but no content file exists |
| `TargetPath`, `TargetName` | absolute install path (exact case) and file name |

Empty revisions (`"Changes":[]`) occur and are kept.

### Which changes carry content

In a seed, only the **last change of each file** carries content. It is a seed (`IsSeed: true`,
`Composite: false`), and its `.patch` file is the whole file, with SHA-1 equal to `VersionHash`. Every earlier
change of that file is a placeholder. The head revision therefore never has a placeholder; updatemgrd refuses
to update to one ("can not be upated to a version that has a placeholder patch").

All repositories of the seeds compared (2014, 2016, 2019, 2022, 2024) follow this rule, with one exception: in
`data-10282016.zip`, one file (`kop-v2-engine`, `Kiosk Engine\bin\Redbox.Services.KioskBrokerServices.KioskShared.dll`)
keeps seed content in three revisions. updatemgrd accepts both forms.

A seed may also carry **deltas after a file's seed** (`IsSeed: false`, content = VCDIFF from `xdelta3 -e`).
updatemgrd starts each file's chain at its first non-placeholder change, which must be a seed. It applies every
later change with `xdelta3.exe -f -d -s <working> <patch> <out>` and checks SHA-1 = `VersionHash` after each
step. Redbox's seeds contain no deltas; the builder writes them only for revisions you add (below), using
`-S none`, because the kiosk's xdelta3 3.0 rejects the LZMA secondary compressor.

## Building seeds

```sh
# compose
docker compose run --rm -v "$PWD/work:/work" rb-admin python -m redbox_backend admin build-seed /work --all
# WSLC
.\scripts\rb.ps1 build-seed D:\seeds /work --all
```

```
admin build-seed <out.zip | folder> [--repo NAME[@REV] ... | --all] [--edition NAME]
                 [--add REPO DIR TARGET_PATH ...] [--label REPO=LABEL ...] [--no-deltas]
                 [--retarget OLD=NEW ...] [--date MMDDYYYY] [--allow-placeholders]
admin check-seed <zip> [--compare REFERENCE.zip] [--no-apply]
```

- `out`: a zip path, or a folder for `data-<MMDDYYYY>.zip` (the name the importer reads the date from).
  `--date` also sets the entries' timestamps; the default is now.
- `--repo NAME[@REV]`: a repository at a revision, given as a hash prefix or a label. The default is the
  served head, or the head pinned with `admin pin`. `--all` takes every repository at its head.
- `--edition NAME`: take the history of an imported seed instead of the served history. For example,
  `--all --edition data-572024` rebuilds that seed.
- `--add REPO DIR TARGET_PATH`: add your own content as a new revision on top of REPO. REPO's served head is
  the base (or the `--repo` revision); a repository that doesn't exist yet starts empty.
  - Files under `DIR` go to `TARGET_PATH`, with subfolders appended.
  - Unchanged files are left out.
  - A changed file is a VCDIFF delta against the base version when that is less than half its size, otherwise
    a seed. `--no-deltas` makes every file a seed.
  - `--label REPO=LABEL` labels the revision.
  - The revision hash is the SHA-1 of the repository, the base head, the label and the new versions, so the
    same inputs give the same seed.
- `--retarget OLD=NEW`: rewrite target paths starting with `OLD` (case-insensitive), for example to stage a
  seed into a test folder. Patch names follow the new paths.
- `--allow-placeholders`: keep files whose content cannot be rebuilt as placeholders, instead of failing.
  updatemgrd refuses such a seed if one of them is in the head revision.

Content is rebuilt from the store's blobs (`repo.reconstruct_head`: seed plus xdelta3, SHA-1 checked at every
step). A file that cannot be rebuilt stops the build.

The dashboard's **Build a seed zip** builds from the repositories at chosen revisions. The zip goes into the
data volume (`/data/exports`) with a download link. Add new content first with `admin publish` (or the Update
Service page's publish form), or use `build-seed --add`.

`admin check-seed` checks any seed, real or built:

- `.active`/`.current` are 40-zero JSON strings;
- the revlog is contiguous and parses;
- every content file exists, and no content file is unreferenced;
- the head has no placeholders, and each file's chain starts with a seed;
- unless `--no-apply`, every file is rebuilt as updatemgrd does and its SHA-1 is checked.

`--compare` diffs it entry by entry against another seed: names, order, content CRC and size, and zip
attributes.

## How the builder was checked

- `check-seed` passes on the real `data-02032014.zip` and `data-572024.zip`. Every file of every repository was
  rebuilt and its SHA-1 checked.
- `build-seed --all --edition data-572024` against the real `data-572024.zip`:
  - all 2,045 entries have the same names, the same order and the same zip attributes;
  - every entry has the same content (CRC and size), so every revlog is byte-identical and every content file
    identical.
  - Only the compressed bytes and the timestamps differ.
- The same comparison for `data-02282014`, `data-452019` and `data-02182022`: identical, except `dvd-data`,
  which the importer keeps only from the newest seed (`--dvd-data latest`).
  - `data-10282016`: identical except the 2016 quirk above (two revlog entries, and seven content files
    the builder leaves out).
- Round trip: a built seed (an imported repository, an added revision with a delta, a new repository) was
  imported back as an edition. Every file rebuilt to the expected SHA-1.
  - `tests/test_seedzip.py` does the same with a synthetic repository, and also checks layout, attributes,
    determinism, and the checker on a broken seed.
- Real updatemgrd code (`tests/clients/run-updatemgr-harness.ps1 -Rounds 0 -Activate ...`, the decompiled
  `Redbox.UpdateManager.Environment` and the kiosk's `xdelta3.exe`). The zip was unpacked into
  `.store\.repository` and staged and activated with `RepositoryService.UpdateToHead` / `ActivateToHead`:
  - the small test seed: the delta was applied by the kiosk's xdelta3, `.active` = head, `.label` set;
  - the full rebuilt `data-572024` (all 12 repositories, retargeted from `C:\` to a scratch folder): every
    repository staged and activated with SHA-1 checks passing.

Not covered:

- the store installer itself, which unpacks the zip and runs "Activate Image": `provisioning/installer` recreates
  it and was run with Redbox's `data-572024.zip`, not yet with a built seed (see its README, "Verification");
- a real kiosk booting from a built seed.
