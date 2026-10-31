import { createContext, useContext, useEffect, useState, type ReactNode } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Alert, Button, Center, Loader, Paper, PasswordInput, Stack, Text, TextInput, Title } from "@mantine/core";
import { useForm } from "@mantine/form";
import { IconAlertTriangle } from "@tabler/icons-react";
import { api, ApiError } from "./api";

export type Me = { user: string; role: "admin" | "operator" | "viewer"; bootstrap: boolean };
const MeContext = createContext<Me | null>(null);

export const useMe = () => useContext(MeContext)!;
/** may the signed-in user make changes ("change") or manage accounts and tokens ("admin")? */
export const useCan = () => {
  const me = useMe();
  return (what: "change" | "admin" = "change") => me.role === "admin" || (what === "change" && me.role === "operator");
};

export function AuthGate({ children }: { children: ReactNode }) {
  const qc = useQueryClient();
  const [loggedOut, setLoggedOut] = useState(false);
  const me = useQuery({ queryKey: ["me"], queryFn: () => api<Me>("/me"), retry: false, staleTime: Infinity });
  useEffect(() => {
    const h = () => { setLoggedOut(true); qc.clear(); };
    window.addEventListener("rb:logged-out", h);
    return () => window.removeEventListener("rb:logged-out", h);
  }, [qc]);
  if (me.isPending) return <Center h="100vh"><Loader color="rubine" /></Center>;
  if (loggedOut || me.isError || !me.data) return <Login onDone={() => { setLoggedOut(false); me.refetch(); }} />;
  return <MeContext.Provider value={me.data}>{children}</MeContext.Provider>;
}

function Login({ onDone }: { onDone: () => void }) {
  const form = useForm({ initialValues: { username: "admin", password: "" } });
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const status = useQuery({ queryKey: ["status"], queryFn: () => api<{ configured: boolean }>("/status") });
  const submit = form.onSubmit(async (v) => {
    setBusy(true);
    setError(null);
    try {
      await api("/login", { body: v });
      form.setFieldValue("password", "");
      onDone();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  });
  return (
    <div className="rb-dots" style={{ minHeight: "100vh" }}>
      <Center className="rb-main" mih="100vh" p="md">
        <Paper w={380} p="xl" bg="dark.7">
          <form onSubmit={submit}>
            <Stack gap="md">
              <div className="rb-logo"><span className="mark">r.</span><span className="word">redbox<i>.</i></span></div>
              <div>
                <Title order={3}>Control center</Title>
                <Text size="sm">Sign in to the self-hosted kiosk backend.</Text>
              </div>
              {status.data && !status.data.configured && (
                <Alert color="yellow" icon={<IconAlertTriangle size={18} />}>
                  No admin password is set yet. Run <code>scripts/set-admin-password.sh</code> on the server.
                </Alert>
              )}
              <TextInput label="User name" autoComplete="username" required {...form.getInputProps("username")} />
              <PasswordInput label="Password" autoComplete="current-password" required autoFocus {...form.getInputProps("password")} />
              {error && <Alert color="red" variant="light">{error}</Alert>}
              <Button type="submit" loading={busy} fullWidth size="md">Sign in</Button>
            </Stack>
          </form>
        </Paper>
      </Center>
    </div>
  );
}
