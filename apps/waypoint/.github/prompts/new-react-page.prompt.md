---
description: Create a new React page with React Router v7 patterns
---

# New React Page: {{name}}

Create a new page following React Router v7 patterns with TypeScript and Tailwind.

## Requirements

- Page name: `{{name}}`
- Route path: `/{{path}}`
- Description: {{description}}

## Create these files

### 1. `web/app/routes/{{path}}.tsx`

```tsx
import type { Route } from "./+types/{{path}}";

// Loader for data fetching (runs before render)
export async function loader({ request }: Route.LoaderArgs) {
  // Fetch data from API
  const response = await fetch(`${import.meta.env.VITE_API_URL || ""}/api/...`);
  return { data: await response.json() };
}

// Page component
export default function {{Name}}({ loaderData }: Route.ComponentProps) {
  return (
    <div className="container mx-auto p-4">
      <h1 className="text-2xl font-bold mb-4">{{name}}</h1>
      {/* Page content */}
    </div>
  );
}
```

### 2. Update `web/app/routes.ts`

Add the route:

```ts
route("{{path}}", "./routes/{{path}}.tsx"),
```

### 3. Add navigation link

Update `web/app/root.tsx` to include a nav link if needed:

```tsx
<NavLink
  to="/{{path}}"
  className={({ isActive }) => (isActive ? "..." : "...")}
>
  {{ Name }}
</NavLink>
```

## Patterns to follow

- Use `loader` for server-side data fetching
- Use `action` for form submissions (POST/PUT/DELETE)
- Style with Tailwind utility classes
- Wrap API calls with `traced()` from `~/lib/telemetry` for OTEL
- Use TypeScript strictly
