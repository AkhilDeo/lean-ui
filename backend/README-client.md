# Kimina client

Client SDK to interact with Kimina Lean server. 

Example use:
```python
from kimina_client import KiminaClient, Snippet

# Specify LEAN_SERVER_API_KEY in your .env or pass `api_key`.
# Default `api_url` is https://projectnumina.ai
client = KiminaClient()

# If running locally use:
# client = KiminaClient(api_url="http://localhost:80")

client.check("#check Nat")
client.check(
    "import FormalConjectures.Util.ProblemImports\n#check Nat",
    environment="auto",
)
client.check(
    "theorem foo (x : Int) : x = x := by sorry",
    include_sorry_details=True,
)
client.environments()
submit = client.async_check(
    [Snippet(id="fc-job", code="import FormalConjectures.Util.ProblemImports\n#check Nat")],
    environment="auto",
)
poll = client.async_poll(submit.job_id)
metrics = client.async_metrics(include_environments=True)
health = client.environment_health()
```

`environment` is optional. If you omit it, the public gateway keeps the legacy default and verifies against `mathlib-v4.15`.

Set `include_sorry_details=True` on `client.check(...)` or `client.async_check(...)`
to ask the microservice for richer per-hole `sorries` entries. When enabled, each
hole includes:

- flat coordinates: `line`, `column`, `endLine`, `endColumn`
- legacy nested coordinates: `pos`, `endPos`
- `goal`
- `localContext`
- string `proofState`
- numeric `proofStateId` for proof-step replay compatibility

For async gateway requests:

- `client.async_check(...)` submits to `POST /api/async/check`
- returned job ids are gateway-scoped and look like `mathlib-v4.27:<job_id>`
- `client.async_poll(job_id)` accepts the wrapped job id directly
- `client.async_metrics(include_environments=True)` returns aggregate queue totals plus optional per-environment breakdown
- `client.environment_health()` checks the deployed environment metadata through the public gateway

## Backward client

```python
from kimina_client import Lean4Client

client = Lean4Client()

client.verify("#check Nat")
```
