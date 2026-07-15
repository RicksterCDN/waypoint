# Add Tracing to Code

Add OpenTelemetry tracing to new or existing code.

## Request

Add proper tracing instrumentation following the project patterns.

## Backend (Python)

### Service Methods

Use the `@trace` decorator from `app/common/tracer.py`:

```python
from app.common.tracer import trace

class MyService:
    @trace
    async def my_method(self, param: str) -> Result:
        # Method is automatically traced with span name "my_method"
        return await self._do_work(param)
```

### Route Handlers

Use `trace_span()` context manager for request-level tracing:

```python
from app.common.tracer import trace_span

@router.post("/", response_model=Item)
async def create_item(data: ItemCreate, service: ItemService = Depends(get_service)):
    with trace_span("create_item"):
        return await service.create(data)
```

### Adding Attributes to Spans

```python
from opentelemetry import trace as otel_trace

with trace_span("process_order") as span:
    span.set_attribute("order.id", order_id)
    span.set_attribute("order.total", total)
    result = await process(order)
```

## Frontend (TypeScript)

### Using traced() Wrapper

Wrap API calls with `traced()` from `lib/telemetry.ts`:

```typescript
import { traced } from "~/lib/telemetry";

export async function fetchItems() {
  return traced("fetch-items", async () => {
    const response = await fetch("/api/items");
    return response.json();
  });
}
```

## Verification

After adding tracing:

1. Run `aspire run`
2. Trigger the traced code
3. Check Aspire Dashboard (<http://localhost:15888>) for new spans
