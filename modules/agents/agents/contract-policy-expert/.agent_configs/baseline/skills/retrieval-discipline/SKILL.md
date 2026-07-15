# Retrieval discipline

Stable description: Procedure for retrieving contract, policy, rate-card, pricing, and prior-finding evidence from FoundryIQ before using fallback evidence tools.

## Procedure

1. Identify the invoice id and any supplier, contract, SKU, lot, batch, rate-card, policy, or prior-finding identifiers explicitly present in the user request or retrieved context.
2. Query the `knowledge_base` MCP retrieval tool first whenever it is available. Include the invoice id and all explicit identifiers that can narrow retrieval.
3. Do not invent missing identifiers to make a retrieval query look more specific. If an identifier is not supplied or retrieved, leave it out.
4. Use `gather_contract_policy_evidence` only when `knowledge_base` retrieval is unavailable, errors, times out, or cannot be reached.
5. Base evidence claims only on retrieved knowledge-base or fallback-tool output. Never infer contract terms, pricing, policies, findings, or dispositions from general knowledge.
6. When retrieval returns no relevant or citable material, return an empty `evidence` list and describe unresolved questions in `unsupported` rather than guessing.
7. Keep each claim traceable to a stable `source_ref`, such as a contract clause, policy id, rate-card id, pricing schedule id, or prior finding id.
