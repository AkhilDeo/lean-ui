# Docs

Server docs: swagger + redoc

Public gateway additions:
- `POST /api/check` accepts optional `environment`
- `POST /api/check` also accepts optional `include_sorry_details`
- `POST /verify` accepts optional `environment`
- `POST /verify` also accepts optional `include_sorry_details`
- `POST /api/async/check` accepts optional `environment`
- `POST /api/async/check` also accepts optional `include_sorry_details`
- `GET /api/environments` lists the supported Lean runtimes
- `GET /api/environments/health` validates deployed environment metadata through the gateway
- Omitting `environment` preserves the default `mathlib-v4.15` behavior
- `environment=auto` routes `FormalConjectures.*` imports to `formal-conjectures-v4.27`
- async job ids returned by the gateway are wrapped as `<environment_id>:<job_id>`

`include_sorry_details=true` opts into richer `sorries` payloads with flat source
coordinates, `goal`, `localContext`, string `proofState`, and numeric
`proofStateId`. Swagger and ReDoc expose the field through the generated OpenAPI
schema.

Client doc -> pypi link
