from __future__ import annotations

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.codestra_middleware_bridge.controllers.api import (
    CodestraMiddlewareBridge,
)


class CodestraLeadsWorkstationBridge(CodestraMiddlewareBridge):
    @http.route(
        "/codestra/middleware/v1/leads-workstation/projection",
        type="http",
        auth="none",
        methods=["POST"],
        csrf=False,
        readonly=False,
    )
    def apply_projection(self):
        auth, payload, error = self._begin(
            "leads_workstation_projection",
            allow_event_replay=True,
        )
        if error:
            return error
        if not isinstance(payload, dict):
            return self._json(422, {"error": "object_required"})

        def run():
            try:
                record, created = (
                    request.env["crm.lead"]
                    .with_user(auth["user"])
                    .apply_leads_workstation_projection(payload)
                )
            except ValidationError as exc:
                return self._json(422, {"error": str(exc)})
            value = {
                "odoo_lead_id": record.id,
                "lead_id": record.codestra_leads_id,
                "version": record.codestra_leads_version,
                "created": created,
            }
            return self._complete(
                auth,
                "leads_workstation_projection",
                value,
                status=201 if created else 200,
            )

        return self._serialized(auth, run)
