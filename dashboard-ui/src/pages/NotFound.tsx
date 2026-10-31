import { Button, Stack, Text, Title } from "@mantine/core";
import { Link } from "react-router-dom";

export function NotFound() {
  return (
    <Stack align="flex-start" gap="sm" mt="xl">
      <Title order={2}>Page not found</Title>
      <Text>There is nothing at this address.</Text>
      <Button component={Link} to="/" variant="default">Back to the overview</Button>
    </Stack>
  );
}
