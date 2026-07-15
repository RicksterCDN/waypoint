import type { Config } from "@react-router/dev/config";

export default {
  // Enable server-side rendering
  ssr: true,
  // Runtime auth and telemetry config comes from the root loader; do not
  // prerender routes that would freeze container environment values at build time.
  prerender: [],
} satisfies Config;
