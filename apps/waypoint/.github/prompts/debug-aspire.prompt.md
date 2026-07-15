---
description: Debug issues using Aspire dashboard
---

# Debug with Aspire

Use the Aspire dashboard to diagnose issues in the running application.

## Quick Diagnostics

### Check Resource Status

1. Open the Aspire dashboard at <http://localhost:15888>
2. Look at the **Resources** tab
3. Verify all services show green (healthy) status

### View Logs

1. Click on a resource (api or web)
2. Switch to **Console Logs** tab
3. Look for errors or warnings

### Trace Requests

1. Go to **Traces** tab
2. Find the failing request
3. Expand to see the full trace waterfall
4. Look for red spans indicating errors

### Check Metrics

1. Go to **Metrics** tab
2. Look at request rates, latencies, error rates

## Common Issues

### API not starting

```bash
# Check Python environment
cd api
uv sync
uv run python -c "import app.main"
```

### Web not loading

```bash
# Check npm build
cd web
npm install
npm run build
```

### CORS errors

- Check `FRONTEND_ORIGINS` in API settings
- Verify the frontend URL is in the allowed origins

### Database connection failed

- Check the connection string environment variable
- Verify the database resource is running in Aspire

## Restart Everything

```bash
# Stop existing instance and restart
aspire run
```

Type `y` when prompted to stop the existing instance.
