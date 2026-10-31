import React from "react";
import ReactDOM from "react-dom/client";
import { BrowserRouter } from "react-router-dom";
import { MantineProvider } from "@mantine/core";
import { ModalsProvider } from "@mantine/modals";
import { Notifications } from "@mantine/notifications";
import { NavigationProgress } from "@mantine/nprogress";
import { DatesProvider } from "@mantine/dates";
import { MutationCache, QueryCache, QueryClient, QueryClientProvider } from "@tanstack/react-query";
import "@mantine/core/styles.css";
import "@mantine/dates/styles.css";
import "@mantine/notifications/styles.css";
import "@mantine/nprogress/styles.css";
import "./styles.css";
import { theme } from "./theme";
import { App } from "./App";
import { notifyError } from "./lib/notify";
import { ApiError } from "./lib/api";

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 15_000,
      refetchOnWindowFocus: false,
      retry: (n, e) => !(e instanceof ApiError && [400, 401, 403, 404].includes(e.status)) && n < 2,
    },
  },
  // failed changes always say so; failed loads show their own error state on the page
  mutationCache: new MutationCache({ onError: (e, _v, _c, m) => { if (!m.options.onError) notifyError(e); } }),
  queryCache: new QueryCache(),
});

ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <MantineProvider theme={theme} defaultColorScheme="dark" forceColorScheme="dark">
      <DatesProvider settings={{ firstDayOfWeek: 0 }}>
        <QueryClientProvider client={queryClient}>
          <ModalsProvider>
            <NavigationProgress color="rubine" size={3} />
            <Notifications position="bottom-right" limit={4} />
            <BrowserRouter>
              <App />
            </BrowserRouter>
          </ModalsProvider>
        </QueryClientProvider>
      </DatesProvider>
    </MantineProvider>
  </React.StrictMode>,
);
