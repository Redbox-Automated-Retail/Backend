# Redbox control center frontend: conventions

Vite + React 18 + TypeScript, Mantine 7 (core, dates, form, modals, notifications, nprogress), TanStack Query 5,
TanStack Table/Virtual (inside `ServerTable`), react-router 6, zod, dayjs, @tabler/icons-react. Served by
`redbox_backend/dashboard` (built into `redbox_backend/dashboard/static`). Node comes from fnm: in PowerShell run
`fnm env --use-on-cd | Out-String | Invoke-Expression` first.

The user's bar: it must feel like professional software. Their explicit complaints about the previous version:
gray text on the dark background (hard to read), pages loading with no indicator, slow loads, unsortable tables,
missing deletes, typing date strings, admin operations mixed into content pages.

## Rules for every page

- **Contrast**: never `c="dimmed"` or gray text. Body text is the default (`dark.0`); secondary text `c="dark.1"`
  (still high contrast). Text sits on panels (`Paper`/`Card` with `bg="dark.7"`), not directly on the dotted background
  except page headers.
- **Loading**: every load shows something. Tables: `ServerTable` / `LocalTable` (skeleton rows, inline progress while
  refetching). Other content: Mantine `Skeleton`. The top progress bar runs automatically for any query/mutation
  (mark background polls `meta: { silent: true }`). Long jobs (imports, builds) show a `Progress` bar plus their log.
- **Errors**: failed loads show `ErrorState` (with retry); failed changes show a red notification automatically
  (mutations) or call `notifyError`. Successes: `notifyOk("...")`.
- **Tables**: always sortable. Big lists: `ServerTable` (server-side sort/filter/paging, virtual scroll) with
  `useUrlSort`. Small lists: `LocalTable` with `value` accessors on sortable columns. Rows open details on click
  (Drawer or detail page); actions are buttons.
- **CRUD**: create / edit in a `Drawer` (position="right", size="lg") or `Modal` with `@mantine/form` + zod
  (`zodResolver` from `mantine-form-zod-resolver`), field-level validation messages, a disabled Save until
  something changed (`form.isDirty()`), loading state on Save. Deletes always confirm (`confirm()` from
  `@/lib/notify`, danger) and say what goes with it. After a change: `queryClient.invalidateQueries(...)`.
- **Dates and times**: never a text box. `DateField` / `DateTimeField` / `TimeField` from `@/components/fields`
  (or Mantine dates pickers). Send ISO dates ("2026-01-31") and "HH:mm" times; the server converts.
- **Empty states**: `EmptyState` with a sentence that says what would fill it and how.
- **URLs**: tabs are route segments (`RouteTabs`), filters and search are URL params (`UrlSearch`, `useUrlParam`,
  `useUrlSort`) so views can be linked and survive reloads.
- **Permissions**: `useCan()("change")` for operator+ actions, `useCan()("admin")` for accounts/tokens; hide or disable
  controls the user can't use (viewers see read-only pages).
- **Layout**: `PageHeader` (title, description, actions) at the top, `StatCards` where numbers matter, `Section` for
  panels, `Facts` for label/value grids, `Status` for coloured status words. Responsive: Mantine `SimpleGrid`/`Group`
  with breakpoints; nothing should need horizontal page scroll at 1280px.
- **Keyboard**: rows are focusable and open with Enter (built into the tables); forms submit with Enter.
- **No money**: the kiosks cannot take real payments. Never show revenue, amounts as income, or payment KPIs.

## Shared pieces

- `@/lib/api`: `api(path, {method, body, form})`, `qs({...})`, `Page<T>` = `{rows, total}`.
- `@/lib/auth`: `useMe()`, `useCan()`.
- `@/lib/notify`: `notifyOk`, `notifyError`, `confirm({title, body, confirmLabel, danger})`.
- `@/lib/format`: `num`, `ago`, `dateTime`, `date`, `short`, `bytes`, `duration`, `fromRedboxDate`, `dayjs`.
- `@/components/DataTable`: `ServerTable`, `LocalTable`, `Column<T>`, `useUrlSort`, `EmptyState`, `ErrorState`.
- `@/components/ui`: `PageHeader`, `StatCards`, `UrlSearch`, `useUrlParam`, `RouteTabs`, `Toolbar`, `Status`, `Section`, `Facts`.
- `@/components/fields`: `DateField`, `DateTimeField`, `TimeField`, `AddressSearch`.

The previous (vanilla JS) dashboard is the functional reference: every feature it had must still be there.
`git show origin/main:redbox_backend/dashboard/static/app.js`. The API is `redbox_backend/dashboard/server.py` and
`redbox_backend/dashboard/control.py`.
