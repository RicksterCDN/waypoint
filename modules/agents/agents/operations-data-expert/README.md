# Operations Data Expert

FabricIQ is currently an explicit stub in the invoice-assurance pipeline.

It keeps the `fabriciq` evidence lane present and contract-valid while the real
Microsoft Fabric / OneLake / warehouse source is being designed. It must not
claim that Fabric, OneLake, Power BI, a warehouse, or a semantic model was
queried.

The current tool returns the shared evidence contract with an empty evidence list
and a summary that marks the lane as temporarily stubbed. The waypoint-recorder and
Assurance Orchestrator can consume that shape safely, but it should not be treated as real
Fabric-backed evidence.
