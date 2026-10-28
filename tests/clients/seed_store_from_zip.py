"""Lay out an update store the way the store installer does with a data-<date>.zip seed.

    python seed_store_from_zip.py <data-MMDDYYYY.zip> <harness root> [repo ...]

Extracts `<repo>/.revlog/*`, `.active`, `.current` and the `.patch` files of the chosen repositories (all when
none are given) into `<root>/.store/.repository`, so run-updatemgr-harness.ps1 can play a kiosk that was just
installed from that seed. The zip is only read.
"""
import pathlib
import sys
import zipfile


def main(zip_path, root, repos):
    target = pathlib.Path(root) / ".store" / ".repository"
    target.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zip_path) as z:
        names = [n for n in z.namelist() if not n.endswith("/")]
        chosen = set(repos) or {n.split("/")[0] for n in names}
        count = 0
        for n in names:
            if n.split("/")[0] in chosen:
                out = target / n
                out.parent.mkdir(parents=True, exist_ok=True)
                with z.open(n) as src, open(out, "wb") as dst:
                    dst.write(src.read())
                count += 1
    print(f"extracted {count} files of {sorted(chosen)} into {target}")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3:])
