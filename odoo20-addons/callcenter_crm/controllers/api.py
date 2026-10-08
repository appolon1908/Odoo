"""Session/API-key authenticated, record-rule scoped Odoo 20 CRM read API.

The Codestra Django dashboard may use an Odoo API key belonging to a narrowly
authorized Odoo user; there is deliberately no public CORS or unauthenticated
CRM data surface and no outbound provider effects.
"""
from odoo import http
from odoo.http import request
from werkzeug.exceptions import BadRequest, Forbidden


ROLES = (
    ("superuser", "callcenter_crm.group_callcenter_superuser"),
    ("supervisor", "callcenter_crm.group_callcenter_supervisor"),
    ("agent", "callcenter_crm.group_callcenter_agent"),
)


def required_role(env):
    for role, xmlid in ROLES:
        if env.user.has_group(xmlid):
            return role
    raise Forbidden("Call Center membership required")


def bounded_page(raw, default, maximum):
    if raw in (None, ""):
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as exc:
        raise BadRequest("Page and limit must be integers") from exc
    if value < 1 or value > maximum:
        raise BadRequest("Pagination parameter out of range")
    return value


def campaign_payload(env, *, page=1, limit=25):
    role = required_role(env)
    Campaign = env["callcenter.campaign"].with_context(active_test=False)
    total = Campaign.search_count([])
    campaigns = Campaign.search([], offset=(page - 1) * limit, limit=limit, order="code,id")
    return {
        "schema_version": 1,
        "role": role,
        "page": page,
        "limit": limit,
        "total": total,
        "campaigns": [
            {
                "id": item.id,
                "code": item.code,
                "name": item.name,
                "state": item.state,
                "active": item.active,
                "campaign_type": item.campaign_type,
            } for item in campaigns
        ],
    }


def lead_payload(env, *, page=1, limit=25, campaign_id=None):
    role = required_role(env)
    domain = [("cc_campaign_id", "!=", False)]
    if campaign_id is not None:
        domain.append(("cc_campaign_id", "=", campaign_id))
    if role == "agent":
        domain.append(("user_id", "=", env.user.id))
    Lead = env["crm.lead"].with_context(active_test=False)
    total = Lead.search_count(domain)
    leads = Lead.search(domain, offset=(page - 1) * limit, limit=limit, order="create_date desc, id desc")
    return {
        "schema_version": 1,
        "role": role,
        "page": page,
        "limit": limit,
        "total": total,
        "leads": [
            {
                "id": item.id,
                "name": item.name,
                "campaign_id": item.cc_campaign_id.id,
                "queue_state": item.queue_state,
                "priority": item.priority,
                "assigned_to_me": item.user_id.id == env.user.id,
            } for item in leads
        ],
    }


class CallCenterReadAPI(http.Controller):
    @staticmethod
    def _response(data):
        return request.make_json_response(
            data, headers=[
                ("Cache-Control", "private, no-store, max-age=0"),
                ("X-Content-Type-Options", "nosniff"),
            ],
        )

    @http.route(
        "/callcenter/api/v1/overview",
        type="http", auth="bearer", bearer_scope="rpc", methods=["GET"], readonly=True,
        save_session=False,
    )
    def overview(self, **kwargs):
        # Counts are calculated through the caller's Odoo ACLs and rules.
        data = campaign_payload(request.env, page=1, limit=25)
        leads = lead_payload(request.env, page=1, limit=1)
        data["service"] = "codestra-odoo20-callcenter"
        data["lead_count_visible"] = leads["total"]
        data["ok"] = True
        return self._response(data)

    @http.route(
        "/callcenter/api/v1/campaigns",
        type="http", auth="bearer", bearer_scope="rpc", methods=["GET"], readonly=True,
        save_session=False,
    )
    def campaigns(self, **kwargs):
        page = bounded_page(request.httprequest.args.get("page"), 1, 100000)
        limit = bounded_page(request.httprequest.args.get("limit"), 25, 50)
        return self._response(campaign_payload(request.env, page=page, limit=limit))

    @http.route(
        "/callcenter/api/v1/leads",
        type="http", auth="bearer", bearer_scope="rpc", methods=["GET"], readonly=True,
        save_session=False,
    )
    def leads(self, **kwargs):
        page = bounded_page(request.httprequest.args.get("page"), 1, 100000)
        limit = bounded_page(request.httprequest.args.get("limit"), 25, 50)
        raw_campaign = request.httprequest.args.get("campaign_id")
        campaign_id = bounded_page(raw_campaign, None, 2147483647) if raw_campaign else None
        return self._response(
            lead_payload(request.env, page=page, limit=limit, campaign_id=campaign_id)
        )
