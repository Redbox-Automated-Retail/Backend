import { createTheme, type MantineColorsTuple } from "@mantine/core";

// Redbox kiosk UI palette (Redbox.Rental.UI/assets/styledictionary.xaml): RubineRed #E42045, WarmPurple #792D8D.
const rubine: MantineColorsTuple = ["#ffe9ee", "#ffd1da", "#fda0b2", "#fb6b87", "#f94063", "#f8264c", "#E42045", "#c8153a", "#b30a32", "#9c0029"];
const purple: MantineColorsTuple = ["#f8eefb", "#ebd9f1", "#d6b0e3", "#c084d5", "#ad5fc9", "#a248c2", "#9c3bbf", "#882ea8", "#792D8D", "#681f7c"];
// Surfaces from near-black to light. In the dark scheme Mantine uses dark[0] for text and dark[2] for "dimmed"
// text: both stay light so secondary text keeps WCAG AA contrast on the panels, the dots and the sidebar.
const dark: MantineColorsTuple = ["#F4F1F6", "#E8E3EC", "#D6D0DC", "#A79FAE", "#3E3645", "#2B2531", "#1C1721", "#151118", "#0F0C12", "#0A080C"];

export const theme = createTheme({
  primaryColor: "rubine",
  primaryShade: { light: 6, dark: 6 },
  colors: { rubine, purple, dark },
  fontFamily: "Montserrat, system-ui, sans-serif",
  fontFamilyMonospace: "ui-monospace, 'Cascadia Mono', Consolas, monospace",
  headings: { fontFamily: "Montserrat, system-ui, sans-serif", fontWeight: "800" },
  defaultRadius: "md",
  cursorType: "pointer",
  focusRing: "auto",
  components: {
    Button: { defaultProps: { fw: 600 } },
    Paper: { defaultProps: { withBorder: true, radius: "lg" } },
    Card: { defaultProps: { withBorder: true, radius: "lg" } },
    Modal: { defaultProps: { centered: true, overlayProps: { backgroundOpacity: 0.65, blur: 3 } } },
    Drawer: { defaultProps: { overlayProps: { backgroundOpacity: 0.55, blur: 2 } } },
    Tooltip: { defaultProps: { withArrow: true, openDelay: 250 } },
    TextInput: { defaultProps: { size: "sm" } },
  },
});
