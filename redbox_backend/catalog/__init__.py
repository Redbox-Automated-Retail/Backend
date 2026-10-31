"""The kiosk catalog: import, edit, build and publish profile.data / inventory.data (docs/catalog.md).

  lua.py        the Lua table literals profile.data stores as values
  inventory.py  the inventory.data archive format
  store.py      import into the database, build the files back, fill cloud_products
  edit.py       titles, product groups, box art and barcodes (admin CLI and dashboard)
  publish.py    build + publish a dvd-data revision and roll it out
"""
