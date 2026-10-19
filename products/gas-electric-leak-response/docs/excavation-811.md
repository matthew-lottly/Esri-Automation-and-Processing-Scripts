# 811 / excavation tickets

There is no single national 811 REST API. One-call centers and ticket-management vendors each expose their own (often authenticated) service, and many utilities
copy active tickets into a feature layer.

* `EXCAVATION_PROVIDER=none` - skip (default).
* `EXCAVATION_PROVIDER=layer` - point `EXCAVATION_LAYER_URL` at a ticket layer; field names in `config/layer_schema.yaml -> excavation_layer`.
* `EXCAVATION_PROVIDER=rest` - generic client in `clients/excavation.py::RestTicketClient`. It assumes a **placeholder contract**
  (`GET url?lat&lon&radius_m` with `Authorization: Bearer <key>` returning `{"tickets": [...]}`). Subclass it and override `request_params()` and `parse_item()` for your provider.

Lookup failure is recorded as a flag ("811 lookup failed"), shown in Report 2, and does not stop the pipeline. Tickets are informational unless `grading.excavation_ticket_escalates` is enabled.
