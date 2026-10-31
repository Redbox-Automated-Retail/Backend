# The kiosk catalog

Kiosks rent from two local files in `C:\Program Files\Redbox\REDS\Kiosk Engine\data`, both shipped in the
`dvd-data` update repository:

| File | Format | Holds | Read by |
| --- | --- | --- | --- |
| `profile.data` | VistaDB 3.5 database | every table a list of `[Key]` -> Lua table literal: `ProductCatalog` (titles), `ProductGroup` (rollups, multi-disc), `ProductType`, `ProductFamily`, `ProductRating`, `Genres`, `Market`, `Store`, `Banner`, `Vendor`, `SubPlatform`, the XREF tables, `Version`; plus `Cache` (box art images) | `Redbox.Rental.Data.ProfileData` |
| `inventory.data` | Redbox archive (`<~look~>` header, 18-byte records sorted by barcode) | barcode -> title id, status, rental count; ~26 million records | `Redbox.ProductLookupCatalog.Archive` (binary search) |

The backend imports both into its database, lets you edit them (dashboard **Catalog** page, `admin catalog-*`),
builds them again and publishes them as a new `dvd-data` revision. Code: `redbox_backend/catalog/`.

## Storage (`schema/60_catalog.sql`)

- **`catalog_tables`**: each `profile.data` table with its VistaDB definition (columns, indexes), in file order.
- **`catalog_rows`** (`tbl`, `key`, `ord`, `lua`, `value`): every row but the box art.
  - `lua` is the stored text and is written back as is.
  - `value` is that text parsed (jsonb), for search and display.
  - `ord` keeps the file's row order.
- **`catalog_images`**: the `Cache` table, with the image bytes in the blob store (`DATA_DIR/blobs`). Rows with
  NULL data (real files have three) stay NULL.
- **`cloud_barcodes`**: `inventory.data`, one row per barcode, with `status`, `rental_count` and `source`:
  - `catalog`: imported;
  - `added`: added or changed on the dashboard or with the CLI;
  - `learned`: from kiosk transactions.

  The `barcode` column uses the ordinal (`C`) collation, the order the engine's binary search needs.
- **`catalog_barcode_dups`**: the earlier records of the 23 barcodes a real `inventory.data` repeats. They are
  written before the kept record, so an unedited catalog comes back byte for byte.
- **`catalog_state`**: what was imported, the database settings (page size, LCID, case sensitivity), the
  `inventory.data` header, and three stamps (`changed_at`, `stamped_at`, the last build).
- **`cloud_products`**: filled from the catalog (`from_catalog`), for the cloud APIs (`api/product/bytitleids`,
  Product Reels):
  - `title_id` is the product id;
  - `product_group_id` is its title rollup group (`ProductGroup` type 1), or the title itself when it has none;
  - `product_ids` maps each product type name to the product id of that edition in the group.

### Lua values (`catalog/lua.py`)

The values are what the engine's `ParseLuaValue` reads (software/src/Rental/Redbox.Rental.Model/ParseLuaValue.cs):
`{ key = value , ... }`, lists `{ "a" , "b" }`, numeric keys `[1000] = true`, `nil`, numbers, strings.

- **Escapes.** Only `\"` and `\r\n` are escapes. Any other backslash is text: descriptions hold `\t` and `\\`,
  which the kiosk shows as written.
- **Strings it cannot read.** A string that ends in a backslash, or has one right before a quote, cannot be
  stored, and edits refuse it.
- **Empty tables.** Empty lists are written `{  }` and empty maps `{}`, as Redbox wrote them.
- **Numbers** keep their text: `sellthru_price = 0.00` stays `0.00`.
- **Round trip.** `encode(decode(v)) == v` holds for every value Redbox wrote: 112,223 of the 112,254 in a real
  file. The other 31 are titles added with a third-party script (`, ` separators); their stored text is kept
  unchanged.

## Import

| Source | Dashboard | CLI |
| --- | --- | --- |
| the `dvd-data` revision served to kiosks (default), or another revision | Catalog, Import & publish, Import | `admin catalog-import [--revision R]` |
| a folder with `profile.data` and `inventory.data` | the same, folder field (a path inside rb-admin) | `admin catalog-import --folder DIR` |

- **It replaces the catalog.** Barcodes that are `added` or `learned` are kept, and win over the imported ones.
- **Timing.** A full kiosk catalog (11,655 titles, 10,677 images, 25,790,285 barcodes) takes about 3 minutes:
  - reading `profile.data` and storing the box art takes most of that;
  - the barcodes are copied straight into `cloud_barcodes` in about a minute (the file is already in order), then
    indexed;
  - `cloud_barcodes` is locked meanwhile.
- **Order is checked.** An `inventory.data` that is not in barcode order is refused: the engine could not find
  barcodes in it either.

## Editing (`catalog/edit.py`)

The dashboard's **Catalog** page has tabs for titles, product groups, barcodes, other tables, and import and
publish. The CLI does the same with `admin catalog-titles`, `catalog-show`, `catalog-title-add`,
`catalog-title-set`, `catalog-title-delete`, `catalog-group-set`, `catalog-group-delete`, `catalog-barcode-set`,
`catalog-barcode-delete` and `catalog-row`. The rules follow the real data and the engine:

- **New titles.**
  - A new title gets the next product id (the highest `ProductCatalog` key + 1).
  - It gets the fields most titles have, in their order, with empty defaults.
  - Release, street, sort and merchandise dates default to today; other dates are `nil`.
- **Dates** must be `yyyyMMdd` or `yyyyMMddHHmmss`. The engine parses them with `ParseExact` and skips a whole
  title it cannot read (an empty string included).
- **OMDb.** With `OMDB_API_KEY` set (`.env`), a title can be looked up on OMDb. That fills the name, plot, cast,
  directors, running time, dates, studio and box office, and the poster becomes the box art. The rating and
  genres are matched against the catalog's own `ProductRating` and `Genres` tables (`Thriller` -> `Suspense`,
  ...).
- **Box art** is stored in `Cache` under the SHA-1 of its bytes. The engine extracts each image name only once
  (`ExtractImages` skips names it already has), so new content needs a new name. An image no title uses any more
  is dropped with its last title.
- **Product groups.**
  - Type 1 is a title rollup (the DVD, Blu-ray and 4K editions of one title), type 3 multi-disc, type 6 the
    multi-disc workaround.
  - The XREF tables (`TitleRollupXREF`, `MultiDiscXREF`, `MultiDiscWorkaroundXREF`) are kept in step. The engine
    rebuilds them from `ProductGroup` anyway.
  - Deleting a title removes it from its groups (and a group left empty).
- **Barcodes** are digits only: `inventory.data` stores them as a leading-zero count and a number.
- **Other tables** (`Store`, `Market`, `Banner`, ...) are edited as Lua text, checked by parsing it.

Every change to `catalog_rows`, `catalog_images` or `cloud_barcodes` marks the catalog changed (statement
triggers).

## Build and publish (`catalog/store.py`, `catalog/publish.py`)

**Building** writes both files from the database:
- **`profile.data`**: written with vistadb3, a pure-Python VistaDB 3.5 writer (submodule
  `vendor/vistadb3-py`). It uses the imported table definitions, database settings and row order.
- **`inventory.data`**: `cloud_barcodes` in barcode order, with any repeated records first.
- **Stamps.** After a change, the build stamps `Version.generated_on` and the `inventory.data` created-on date
  with the time. It stores both, so builds without a change in between give the same bytes. `profile.data`'s
  replica id is derived from that stamp.

```sh
admin catalog-build /out            # just the files
admin catalog-publish               # a dvd-data revision for every kiosk
admin catalog-publish --market "Denver, CO"   # or --kiosk 12345
admin build-seed /work --catalog    # an installer seed with the catalog as built now
```

**Publishing** adds the files to the `dvd-data` repository, at the folder its head already uses
(`updatesvc/repo.py` `publish_revision`):
- **Deltas.** A changed file goes out as a VCDIFF delta when that is smaller. For one added title with box art
  and a barcode, the deltas were 3.7 MB for `profile.data` (323 MB) and 1.6 KB for `inventory.data` (464 MB).
- **Fresh kiosks.** Kiosks without the repository get the new snapshot.
- **Targets.** For a market or one kiosk, the revision is published unreleased and rolled out to that target
  only (`updatesvc/releases.py`). Other kiosks stay where they are until it is rolled out to them, or to
  everyone.
- **Unchanged files** are skipped. A catalog with no change since the last publish gives no new revision.

## Verified

On the real catalog of a kiosk image (profile.data of January 2025, inventory.data of December 2024):

- **Lua codec.** Every value parses.
- **Unedited rebuild.** Rebuilt from the database without edits:
  - `inventory.data` is byte-identical (SHA-1 `B0CDA35C...`, 23 repeated barcodes included);
  - the VistaDB DLL dumps `profile.data` identically to the original (the test oracle, `vendor/vistadb3-py/oracle`)
    and finds every row through `IX_Key`.
- **Import from `dvd-data`.** Importing the published `dvd-data` head and rebuilding gave the same again.
- **updatemgrd** (`tests/clients/run-updatemgr-harness.ps1`, the real Update Manager code and the kiosk's
  `xdelta3.exe`), in two steps:
  - a fresh store got the base revision as a seed;
  - after the edit it got the 3.7 MB delta revision, staged it with SHA-1 checks and activated it. The activated
    files matched the build.
- **The engine** (`tests/clients/run-catalog-engine-harness.ps1`: the engine's own `ProfileData.LoadDataInstance`
  and `Archive.Find`):
  - every table loaded completely (11,656 titles, 9,489 groups, 76,172 stores, ...);
  - the added title had its fields, rollup and box art;
  - its barcode and the repeated ones resolved.
- **Seeds.** `admin build-seed --catalog` passed `admin check-seed`.
- **Tests.** `tests/test_catalog.py`: the codec, `inventory.data`, and import -> edit -> build on a synthetic
  catalog. Set `RB_CATALOG_DIR` to a folder with real files to also check every real value.

## Limits

- **Box art is stored as given** (no resizing). Redbox's images are 262x370 JPEGs (399 of 400 sampled); posters
  from OMDb are usually 300 pixels wide. Resize them before uploading if the size matters.
- **Import locks the barcodes** for about a minute.
- **The Kiosk Client's product records are separate.** `profile_products` (IoT `GetRecordChanges`) is a different
  format and is not filled from the catalog.
