import { useState } from "react";
import { Link, useParams } from "react-router-dom";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Alert, Anchor, Badge, Button, Checkbox, Code, CopyButton, Drawer, Group, Modal, PasswordInput, Select, SimpleGrid, Skeleton,
  Stack, Switch, Text, TextInput,
} from "@mantine/core";
import { useForm } from "@mantine/form";
import { zodResolver } from "mantine-form-zod-resolver";
import { z } from "zod";
import { IconAlertTriangle, IconCheck, IconCopy, IconKey, IconPlus } from "@tabler/icons-react";
import { LocalTable, ErrorState, EmptyState } from "@/components/DataTable";
import { Facts, PageHeader, RouteTabs, Section, Status } from "@/components/ui";
import { api } from "@/lib/api";
import { useCan, useMe } from "@/lib/auth";
import { confirm, notifyOk } from "@/lib/notify";
import { ago, dateTime, num } from "@/lib/format";
import { changeOwnPassword } from "./ownPassword";

type Role = "admin" | "operator" | "viewer";
type User = { username: string; display_name: string | null; role: Role; enabled: boolean; created_at?: string; created_by?: string | null;
  password_changed_at?: string | null; last_login_at: string | null; bootstrap?: boolean };
type Users = { bootstrap: { username: string; role: "admin"; configured: boolean; last_login_at: string | null }; users: User[]; roles: Role[] };
type Token = { id: number; name: string; scopes: string[] | null; created_at: string; last_used_at: string | null; revoked_at: string | null };
type Access = {
  staff: { username: string; active: boolean }[];
  cloud_api: { enforced: boolean; keys: string[] };
  iot_certs: { active: number; revoked: number };
  mqtt_client_cert: boolean;
  update_service_check: boolean;
};

const ROLE_TEXT: Record<Role, string> = {
  admin: "Everything, including dashboard accounts and CI tokens",
  operator: "Every change except accounts and CI tokens",
  viewer: "Read only: sees every page, changes nothing but their own password",
};
const ROLE_DATA = (Object.keys(ROLE_TEXT) as Role[]).map((r) => ({ value: r, label: r[0].toUpperCase() + r.slice(1) }));

// ------------------------------------------------------------------------------------------- users

const userSchema = (isNew: boolean) => z.object({
  username: isNew ? z.string().trim().min(1, "Choose a user name").max(64, "At most 64 characters").regex(/^\S+$/, "No spaces") : z.string(),
  display_name: z.string().max(120),
  role: z.enum(["admin", "operator", "viewer"], { errorMap: () => ({ message: "Pick a role" }) }),
  password: isNew ? z.string().min(10, "At least 10 characters") : z.string().refine((v) => !v || v.length >= 10, "At least 10 characters, or leave it empty"),
  enabled: z.boolean(),
});

function UserForm({ user, onDone }: { user: User | null; onDone: () => void }) {
  const qc = useQueryClient();
  const me = useMe();
  const isNew = !user;
  const form = useForm({
    initialValues: { username: user?.username || "", display_name: user?.display_name || "", role: (user?.role || "operator") as Role, password: "", enabled: user?.enabled ?? true },
    validate: zodResolver(userSchema(isNew)),
  });
  const save = useMutation({
    mutationFn: (v: typeof form.values) => isNew
      ? api("/users", { body: { username: v.username.trim(), display_name: v.display_name, role: v.role, password: v.password } })
      : api(`/users/${encodeURIComponent(user!.username)}`, { method: "PUT",
        body: { display_name: v.display_name, role: v.role, enabled: v.enabled, ...(v.password ? { password: v.password } : {}) } }),
    onSuccess: () => { notifyOk(isNew ? "User added" : "Saved"); qc.invalidateQueries({ queryKey: ["users"] }); onDone(); },
  });
  const del = useMutation({
    mutationFn: () => api(`/users/${encodeURIComponent(user!.username)}`, { method: "DELETE" }),
    onSuccess: () => { notifyOk(`${user!.username} deleted`); qc.invalidateQueries({ queryKey: ["users"] }); onDone(); },
  });
  const self = user?.username === me.user;
  return (
    <form onSubmit={form.onSubmit((v) => save.mutate(v))}>
      <Stack>
        {isNew && <TextInput label="User name" required autoComplete="off" data-autofocus {...form.getInputProps("username")} />}
        <TextInput label="Display name" description="Optional; shown next to the user name" {...form.getInputProps("display_name")} />
        <Select label="Role" data={ROLE_DATA} allowDeselect={false} disabled={self} description={self ? "You cannot change your own role" : ROLE_TEXT[form.values.role]} {...form.getInputProps("role")} />
        <PasswordInput label={isNew ? "Password" : "New password"} required={isNew} autoComplete="new-password"
          description={isNew ? "At least 10 characters" : "Leave empty to keep the current password"} {...form.getInputProps("password")} />
        {!isNew && <Switch label="Account enabled" description="A disabled account cannot sign in" disabled={self} {...form.getInputProps("enabled", { type: "checkbox" })} />}
        <Group justify="space-between" mt="sm">
          {!isNew ? (
            <Button color="red" variant="light" disabled={self} loading={del.isPending} onClick={async () => {
              if (await confirm({ title: `Delete ${user!.username}?`, danger: true, confirmLabel: "Delete user",
                body: "The account is removed and can no longer sign in. What it changed stays in the audit log." })) del.mutate();
            }}>Delete user</Button>
          ) : <span />}
          <Group>
            <Button variant="default" onClick={onDone}>Cancel</Button>
            <Button type="submit" loading={save.isPending} disabled={!isNew && !form.isDirty()}>{isNew ? "Add user" : "Save"}</Button>
          </Group>
        </Group>
      </Stack>
    </form>
  );
}

function UsersTab() {
  const can = useCan();
  const me = useMe();
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["users"], queryFn: () => api<Users>("/users") });
  const [edit, setEdit] = useState<User | null | "new">(null);
  const toggle = useMutation({
    mutationFn: (u: User) => api(`/users/${encodeURIComponent(u.username)}`, { method: "PUT", body: { enabled: !u.enabled } }),
    onSuccess: (_d, u) => { notifyOk(`${u.username} ${u.enabled ? "disabled" : "enabled"}`); qc.invalidateQueries({ queryKey: ["users"] }); },
  });
  const d = q.data;
  const rows: User[] | undefined = d && [
    { username: d.bootstrap.username, display_name: null, role: "admin", enabled: d.bootstrap.configured, last_login_at: d.bootstrap.last_login_at, bootstrap: true },
    ...d.users,
  ];
  const admin = can("admin");
  return (
    <Stack gap="lg">
      {admin && <Group><Button leftSection={<IconPlus size={16} />} onClick={() => setEdit("new")}>Add a user</Button></Group>}
      <LocalTable<User> rows={rows} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => (r.bootstrap ? "\0" : "") + r.username}
        onRowClick={admin ? (r) => { if (!r.bootstrap) setEdit(r); } : undefined} columns={[
          { id: "username", header: "User", value: (r) => r.username, cell: (r) => (
            <Group gap={8} wrap="nowrap">
              <Text fw={700} size="sm">{r.username}</Text>
              {r.display_name && <Text size="sm" c="dark.1">{r.display_name}</Text>}
              {r.bootstrap && <Badge size="sm" variant="light" color="gray">from .env</Badge>}
              {r.username === me.user && <Badge size="sm" variant="light" color="rubine">you</Badge>}
            </Group>
          ) },
          { id: "role", header: "Role", value: (r) => r.role, cell: (r) => <Text size="sm" tt="capitalize" fw={600}>{r.role}</Text> },
          { id: "enabled", header: "Status", value: (r) => (r.enabled ? 1 : 0), cell: (r) => r.enabled ? <Status kind="ok">Active</Status> : <Status kind="off">{r.bootstrap ? "No password set" : "Disabled"}</Status> },
          { id: "last_login_at", header: "Last sign-in", value: (r) => r.last_login_at, cell: (r) => ago(r.last_login_at) },
          { id: "created", header: "Added", value: (r) => r.created_at, cell: (r) => r.bootstrap ? "-" : <Text size="sm">{dateTime(r.created_at)}{r.created_by ? ` by ${r.created_by}` : ""}</Text> },
          { id: "act", header: "", align: "right", cell: (r) => r.bootstrap || !admin ? null : (
            <Group gap={6} justify="flex-end" wrap="nowrap">
              <Button size="xs" variant="default" onClick={() => setEdit(r)}>Edit</Button>
              <Button size="xs" variant="light" color={r.enabled ? "yellow" : "teal"} disabled={r.username === me.user}
                loading={toggle.isPending && toggle.variables?.username === r.username} onClick={() => toggle.mutate(r)}>{r.enabled ? "Disable" : "Enable"}</Button>
            </Group>
          ) },
        ]} />
      <Section title="Roles">
        <Stack>
          <Facts cols={3} items={(Object.keys(ROLE_TEXT) as Role[]).map((r) => [r, ROLE_TEXT[r]])} />
          <Text size="sm">
            The account from <Code>.env</Code> (ADMIN_USERNAME / ADMIN_PASSWORD_HASH) is always an admin and is read-only here; change its password with{" "}
            <Code>scripts/set-admin-password.sh</Code>.{" "}
            {!me.bootstrap && <Anchor component="button" type="button" fw={600} size="sm" onClick={changeOwnPassword}>Change your password</Anchor>}
          </Text>
        </Stack>
      </Section>
      <Drawer opened={edit !== null} onClose={() => setEdit(null)} position="right" size="lg"
        title={<Text fw={800}>{edit === "new" ? "Add a dashboard user" : edit ? `Edit ${edit.username}` : ""}</Text>}>
        {edit !== null && <UserForm key={edit === "new" ? "new" : edit.username} user={edit === "new" ? null : edit} onDone={() => setEdit(null)} />}
      </Drawer>
    </Stack>
  );
}

// ------------------------------------------------------------------------------------------ tokens

const tokenSchema = z.object({
  name: z.string().trim().min(1, "Name the token").max(64).regex(/^[\w.-]+$/, "Letters, digits, dot, dash and underscore"),
  scopes: z.array(z.string()).min(1, "Pick at least one scope"),
});

function TokensTab() {
  const admin = useCan()("admin");
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["tokens"], queryFn: () => api<Token[]>("/releases/tokens") });
  const [creating, setCreating] = useState(false);
  const [created, setCreated] = useState<{ name: string; token: string } | null>(null);
  const form = useForm({ initialValues: { name: "", scopes: ["publish"] }, validate: zodResolver(tokenSchema) });
  const create = useMutation({
    mutationFn: (v: typeof form.values) => api<{ name: string; token: string }>("/releases/tokens", { body: { name: v.name.trim(), scopes: v.scopes } }),
    onSuccess: (x) => { setCreating(false); form.reset(); setCreated(x); qc.invalidateQueries({ queryKey: ["tokens"] }); },
  });
  const revoke = useMutation({
    mutationFn: (name: string) => api("/releases/tokens/revoke", { body: { name } }),
    onSuccess: (_d, name) => { notifyOk(`Token ${name} revoked`); qc.invalidateQueries({ queryKey: ["tokens"] }); },
  });
  return (
    <Stack gap="lg">
      <Text size="sm" fw={500}>
        Bearer tokens for <Code>/api/ci/*</Code> (<Code>scripts/rb-release.py</Code>, the Software repository's release workflow).
        <b> publish</b> uploads releases, <b>deploy</b> rolls out and back; every token can read status. A token is shown once.
      </Text>
      {admin ? <Group><Button leftSection={<IconKey size={16} />} onClick={() => setCreating(true)}>Create a token</Button></Group>
        : <Alert color="gray" variant="light">Only an admin can create or revoke tokens.</Alert>}
      <LocalTable<Token> rows={q.data} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => r.id} columns={[
        { id: "name", header: "Name", value: (r) => r.name, cell: (r) => <Text fw={700} size="sm">{r.name}</Text> },
        { id: "scopes", header: "Scopes", value: (r) => (r.scopes || []).join(","), cell: (r) => <Group gap={4}>{(r.scopes || []).map((s) => <Badge key={s} variant="light" color="gray">{s}</Badge>)}</Group> },
        { id: "created_at", header: "Created", value: (r) => r.created_at, cell: (r) => dateTime(r.created_at) },
        { id: "last_used_at", header: "Last used", value: (r) => r.last_used_at, cell: (r) => ago(r.last_used_at) },
        { id: "state", header: "State", value: (r) => (r.revoked_at ? 0 : 1), cell: (r) => r.revoked_at ? <Status kind="off">Revoked {ago(r.revoked_at)}</Status> : <Status kind="ok">Active</Status> },
        { id: "act", header: "", align: "right", cell: (r) => !r.revoked_at && admin ? (
          <Button size="xs" color="red" variant="light" loading={revoke.isPending && revoke.variables === r.name} onClick={async () => {
            if (await confirm({ title: `Revoke token ${r.name}?`, danger: true, confirmLabel: "Revoke", body: "CI jobs using it stop working at once. This cannot be undone; create a new token to replace it." }))
              revoke.mutate(r.name);
          }}>Revoke</Button>
        ) : null },
      ]} empty={<EmptyState title="No tokens">Create one for the release workflow; it is stored as the CI secret RB_API_TOKEN.</EmptyState>} />
      <Modal opened={creating} onClose={() => setCreating(false)} title={<Text fw={800}>Create a CI token</Text>}>
        <form onSubmit={form.onSubmit((v) => create.mutate(v))}>
          <Stack>
            <TextInput label="Name" placeholder="ci-publish" required data-autofocus {...form.getInputProps("name")} />
            <Checkbox.Group label="Scopes" {...form.getInputProps("scopes")}>
              <Stack gap={6} mt={6}>
                <Checkbox value="publish" label="publish: upload releases" />
                <Checkbox value="deploy" label="deploy: roll out and roll back" />
              </Stack>
            </Checkbox.Group>
            <Group justify="flex-end"><Button variant="default" onClick={() => setCreating(false)}>Cancel</Button><Button type="submit" loading={create.isPending}>Create token</Button></Group>
          </Stack>
        </form>
      </Modal>
      <Modal opened={!!created} onClose={() => setCreated(null)} closeOnClickOutside={false} size="lg" title={<Text fw={800}>Token {created?.name}</Text>}>
        {created && (
          <Stack>
            <Alert color="yellow" icon={<IconAlertTriangle size={18} />}>Copy it now: it is not shown again. Store it as the CI secret <Code>RB_API_TOKEN</Code>.</Alert>
            <Code block style={{ wordBreak: "break-all", whiteSpace: "pre-wrap" }}>{created.token}</Code>
            <Group justify="space-between">
              <CopyButton value={created.token}>
                {({ copied, copy }) => <Button variant="default" leftSection={copied ? <IconCheck size={16} /> : <IconCopy size={16} />} onClick={copy}>{copied ? "Copied" : "Copy"}</Button>}
              </CopyButton>
              <Button onClick={() => setCreated(null)}>Done</Button>
            </Group>
          </Stack>
        )}
      </Modal>
    </Stack>
  );
}

// ------------------------------------------------------------------------------------------- staff

const staffSchema = z.object({
  username: z.string().trim().min(1, "Enter a user name").regex(/^\S+$/, "No spaces"),
  password: z.string().min(1, "A new login needs a password"),
  active: z.boolean(),
});
const pwSchema = z.object({ password: z.string().min(1, "Enter the new password") });

function StaffTab() {
  const can = useCan()("change");
  const qc = useQueryClient();
  const q = useQuery({ queryKey: ["access"], queryFn: () => api<Access>("/access") });
  const [adding, setAdding] = useState(false);
  const [pwFor, setPwFor] = useState<{ username: string; active: boolean } | null>(null);
  const addForm = useForm({ initialValues: { username: "", password: "", active: true }, validate: zodResolver(staffSchema) });
  const pwForm = useForm({ initialValues: { password: "" }, validate: zodResolver(pwSchema) });
  const done = (msg: string) => { notifyOk(msg); qc.invalidateQueries({ queryKey: ["access"] }); };
  const save = useMutation({
    mutationFn: (b: { username: string; password?: string; active: boolean }) => api("/staff", { body: b }),
  });
  const del = useMutation({
    mutationFn: (username: string) => api("/staff/delete", { body: { username } }),
    onSuccess: (_d, u) => done(`${u} deleted`),
  });
  const exists = (name: string) => q.data?.staff.some((s) => s.username === name.trim());
  return (
    <Stack gap="lg">
      <Text size="sm" fw={500}>
        Logins for the kiosk's staff screens (<Code>POST api/kiosk/authenticate</Code>). While the list is empty, any non-empty user name and password is accepted.
      </Text>
      {can && <Group><Button leftSection={<IconPlus size={16} />} onClick={() => { addForm.reset(); setAdding(true); }}>Add a staff login</Button></Group>}
      <LocalTable rows={q.data?.staff} loading={q.isPending} error={q.error} onRetry={() => q.refetch()} rowKey={(r) => r.username} columns={[
        { id: "username", header: "User name", value: (r) => r.username, cell: (r) => <Text fw={700} size="sm">{r.username}</Text> },
        { id: "active", header: "Status", value: (r) => (r.active ? 1 : 0), cell: (r) => r.active ? <Status kind="ok">Active</Status> : <Status kind="off">Disabled</Status> },
        { id: "act", header: "", align: "right", cell: (r) => can ? (
          <Group gap={6} justify="flex-end" wrap="nowrap">
            <Button size="xs" variant="default" onClick={() => { pwForm.reset(); setPwFor(r); }}>Set password</Button>
            <Button size="xs" variant="light" color={r.active ? "yellow" : "teal"} loading={save.isPending && save.variables?.username === r.username && !save.variables?.password}
              onClick={() => save.mutate({ username: r.username, active: !r.active }, { onSuccess: () => done(`${r.username} ${r.active ? "disabled" : "enabled"}`) })}>
              {r.active ? "Disable" : "Enable"}
            </Button>
            <Button size="xs" variant="light" color="red" loading={del.isPending && del.variables === r.username} onClick={async () => {
              if (await confirm({ title: `Delete ${r.username}?`, danger: true, confirmLabel: "Delete login",
                body: q.data!.staff.length === 1 ? "This is the last staff login: with none left, the kiosks accept any non-empty user name and password." : "The kiosks stop accepting this login." }))
                del.mutate(r.username);
            }}>Delete</Button>
          </Group>
        ) : null },
      ]} empty={<EmptyState title="No staff logins">Every non-empty user name and password is accepted at the kiosks until you add one.</EmptyState>} />
      <Modal opened={adding} onClose={() => setAdding(false)} title={<Text fw={800}>Add a staff login</Text>}>
        <form onSubmit={addForm.onSubmit((v) => {
          if (exists(v.username)) { addForm.setFieldError("username", "That login exists; use Set password"); return; }
          save.mutate({ username: v.username.trim(), password: v.password, active: v.active }, { onSuccess: () => { setAdding(false); done("Staff login added"); } });
        })}>
          <Stack>
            <TextInput label="User name" required data-autofocus autoComplete="off" {...addForm.getInputProps("username")} />
            <PasswordInput label="Password" required autoComplete="new-password" {...addForm.getInputProps("password")} />
            <Switch label="Active" {...addForm.getInputProps("active", { type: "checkbox" })} />
            <Group justify="flex-end"><Button variant="default" onClick={() => setAdding(false)}>Cancel</Button><Button type="submit" loading={save.isPending}>Add login</Button></Group>
          </Stack>
        </form>
      </Modal>
      <Modal opened={!!pwFor} onClose={() => setPwFor(null)} title={<Text fw={800}>New password for {pwFor?.username}</Text>}>
        <form onSubmit={pwForm.onSubmit((v) => save.mutate({ username: pwFor!.username, password: v.password, active: pwFor!.active },
          { onSuccess: () => { setPwFor(null); done("Password changed"); } }))}>
          <Stack>
            <PasswordInput label="New password" required data-autofocus autoComplete="new-password" {...pwForm.getInputProps("password")} />
            <Group justify="flex-end"><Button variant="default" onClick={() => setPwFor(null)}>Cancel</Button><Button type="submit" loading={save.isPending}>Save password</Button></Group>
          </Stack>
        </form>
      </Modal>
    </Stack>
  );
}

// ------------------------------------------------------------------------------------------ kiosks

function KioskAuthTab() {
  const q = useQuery({ queryKey: ["access"], queryFn: () => api<Access>("/access") });
  const a = q.data;
  if (q.isError) return <Section><ErrorState error={q.error} onRetry={() => q.refetch()} /></Section>;
  return (
    <Section title="Kiosk authentication" actions={<Text size="sm" c="dark.1">from .env, read-only</Text>}>
      {!a ? <Skeleton h={140} /> : (
        <Stack>
          <SimpleGrid cols={{ base: 1, md: 3 }} spacing="lg">
            <Stack gap={4}>
              <Text size="xs" fw={700} tt="uppercase" c="dark.1" style={{ letterSpacing: ".06em" }}>Cloud API keys</Text>
              {a.cloud_api.enforced ? <Status kind="ok">Enforced</Status> : <Status kind="warn">Not enforced</Status>}
              <Text size="sm">{a.cloud_api.keys.length} configured</Text>
              {a.cloud_api.keys.length > 0 && <Group gap={4}>{a.cloud_api.keys.map((k) => <Code key={k}>{k}</Code>)}</Group>}
            </Stack>
            <Stack gap={4}>
              <Text size="xs" fw={700} tt="uppercase" c="dark.1" style={{ letterSpacing: ".06em" }}>MQTT client certificates</Text>
              {a.mqtt_client_cert ? <Status kind="ok">Required</Status> : <Status kind="warn">Not required</Status>}
              <Text size="sm">{num(a.iot_certs.active)} active, {num(a.iot_certs.revoked)} revoked (<Anchor component={Link} to="/provisioning/certificates" fw={600} size="sm">manage</Anchor>)</Text>
            </Stack>
            <Stack gap={4}>
              <Text size="xs" fw={700} tt="uppercase" c="dark.1" style={{ letterSpacing: ".06em" }}>Update Service credentials</Text>
              {a.update_service_check ? <Status kind="ok">Checked (UPDATE_SERVICE_KEY1/2)</Status> : <Status kind="warn">Every store accepted</Status>}
            </Stack>
          </SimpleGrid>
          <Text size="sm">These come from <Code>.env</Code>; change them there and restart the services.</Text>
        </Stack>
      )}
    </Section>
  );
}

export function AccessPage() {
  const { tab = "users" } = useParams();
  return (
    <>
      <PageHeader stats="access" title="Users & access" description="Who can sign in to the dashboard, the kiosks and the APIs" />
      <RouteTabs base="/access" value={tab} tabs={[
        { value: "users", label: "Dashboard users" }, { value: "tokens", label: "CI tokens" },
        { value: "staff", label: "Field-staff logins" }, { value: "kiosks", label: "Kiosk authentication" },
      ]} />
      {tab === "tokens" ? <TokensTab /> : tab === "staff" ? <StaffTab /> : tab === "kiosks" ? <KioskAuthTab /> : <UsersTab />}
    </>
  );
}
