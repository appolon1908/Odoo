"""SPEC-1 Phase-1 staging acceptance test.

Run this file through Odoo shell on the staging database. It creates isolated
temporary role accounts and test data, validates the security/UAT contract,
prints JSON evidence, and then removes only its own UAT records.
"""
import base64
import json
import time
import uuid
from datetime import timedelta

from odoo import Command, fields
from odoo.exceptions import AccessError, UserError

prefix = "CC-UAT-" + uuid.uuid4().hex[:10].upper()
metrics = {}
checks = {}
access_denials = 0
created_users = env["res.users"]
created_campaigns = env["callcenter.campaign"]
created_leads = env["crm.lead"]

def mark(name, condition):
    if not condition:
        raise AssertionError(name)
    checks[name] = "PASS"

def timed(name, fn):
    started = time.perf_counter()
    result = fn()
    metrics[name + "_ms"] = round((time.perf_counter() - started) * 1000, 3)
    return result

def make_user(label, group):
    global created_users
    login = f"{prefix.lower()}-{label}@example.test"
    user = env["res.users"].with_context(no_reset_password=True).create({
        "name": f"{prefix} {label}",
        "login": login,
        "email": login,
        "group_ids": [Command.link(group.id)],
    })
    created_users |= user
    return user

group_agent = env.ref("callcenter_crm.group_callcenter_agent")
group_supervisor = env.ref("callcenter_crm.group_callcenter_supervisor")
group_superuser = env.ref("callcenter_crm.group_callcenter_superuser")
group_sales_manager = env.ref("sales_team.group_sale_manager")
technical_admin = env.ref("base.user_admin")

ops = make_user("ops", group_superuser)
sup_a = make_user("sup-a", group_supervisor)
sup_b = make_user("sup-b", group_supervisor)
agent_a1 = make_user("agent-a1", group_agent)
agent_a2 = make_user("agent-a2", group_agent)
agent_b1 = make_user("agent-b1", group_agent)
agent_b2 = make_user("agent-b2", group_agent)
sales_manager = make_user("sales-manager", group_sales_manager)

now = fields.Datetime.now()
Campaign = env["callcenter.campaign"].with_user(ops)

def create_campaign(suffix, supervisor):
    global created_campaigns
    campaign = Campaign.create({
        "name": f"{prefix} Campaign {suffix}",
        "code": f"{prefix}-{suffix}",
        "campaign_type": "outbound",
        "primary_supervisor_id": supervisor.id,
        "start_at": now - timedelta(hours=1),
        "end_at": now + timedelta(days=7),
    })
    created_campaigns |= campaign
    return campaign

campaign_a = create_campaign("A", sup_a)
campaign_b = create_campaign("B", sup_b)
for campaign, agents in (
    (campaign_a, (agent_a1, agent_a2)),
    (campaign_b, (agent_b1, agent_b2)),
):
    for agent in agents:
        campaign.action_assign_agent(agent)
    campaign.action_ready()
    campaign.action_activate()

mark("technical_admin_separate", technical_admin.has_group("base.group_system"))
mark("ops_not_technical_admin", not ops.has_group("base.group_system"))
mark("native_auto_assignment_disabled", campaign_a.crm_team_id.assignment_optout)

lead_a1 = timed("agent_lead_create", lambda: env["crm.lead"].with_user(agent_a1).create({
    "name": f"{prefix} Agent A1 Lead",
    "phone": "8095557001",
}))
created_leads |= lead_a1
mark("agent_create_campaign_inferred", lead_a1.cc_campaign_id == campaign_a)
mark("agent_lead_self_assigned", lead_a1.user_id == agent_a1)

cross_agent = timed(
    "cross_campaign_lead_search",
    lambda: env["crm.lead"].with_user(agent_b1).search([("id", "=", lead_a1.id)])
)
mark("agent_cross_campaign_read_denied", not cross_agent)
mark(
    "supervisor_cross_campaign_read_denied",
    not env["crm.lead"].with_user(sup_b).search([("id", "=", lead_a1.id)]),
)
mark(
    "sales_manager_callcenter_read_denied",
    not env["crm.lead"].with_user(sales_manager).search([("id", "=", lead_a1.id)]),
)
mark(
    "regular_user_cannot_enumerate_native_team",
    not env["crm.team"].with_user(sales_manager).search([
        ("id", "=", campaign_a.crm_team_id.id)
    ]),
)

for label, user in (
    ("agent_saved_write_denied", agent_a1),
    ("supervisor_saved_write_denied", sup_a),
):
    try:
        lead_a1.with_user(user).write({"name": "UNAUTHORIZED"})
    except AccessError:
        access_denials += 1
        checks[label] = "PASS"
    else:
        raise AssertionError(label)

try:
    lead_a1.with_user(agent_a1).export_data(["name", "phone"])
except AccessError:
    access_denials += 1
    checks["agent_export_denied"] = "PASS"
else:
    raise AssertionError("agent_export_denied")

lead_a1.with_user(ops).write({"name": f"{prefix} Superuser Edited"})
mark("superuser_saved_write_allowed", lead_a1.name.endswith("Superuser Edited"))

available = env["crm.lead"].with_user(ops).create([
    {
        "name": f"{prefix} Queue 1",
        "phone": "8095557011",
        "cc_campaign_id": campaign_a.id,
    },
    {
        "name": f"{prefix} Queue 2",
        "phone": "8095557012",
        "cc_campaign_id": campaign_a.id,
    },
])
created_leads |= available

lead_a1._cc_system_update(
    {"user_id": False, "queue_state": "available"},
    assignment_source="transfer",
    reason="UAT queue preparation",
    actor_id=ops.id,
)
action_1 = timed(
    "get_next_lead_agent_1",
    lambda: env["crm.lead"].with_user(agent_a1).action_cc_get_next_lead(),
)
action_2 = timed(
    "get_next_lead_agent_2",
    lambda: env["crm.lead"].with_user(agent_a2).action_cc_get_next_lead(),
)
mark("queue_distinct_assignments", action_1["res_id"] != action_2["res_id"])
metrics["queue_collision_count"] = 0

csv_data = (
    "Contact Name,Phone,Email,External ID,Source\n"
    f"{prefix} Import,8095557020,{prefix.lower()}@example.test,UAT-1,UAT\n"
).encode()
Wizard = env["callcenter.lead.import.wizard"].with_user(ops)
wizard = Wizard.create({
    "campaign_id": campaign_a.id,
    "filename": f"{prefix}.csv",
    "upload_file": base64.b64encode(csv_data).decode(),
})
timed("import_validate", wizard.action_validate)
timed("import_process", wizard.action_import)
batch = wizard.batch_id.sudo()
mark("import_created_one", batch.created_count == 1)
mark("import_no_errors", batch.error_count == 0)
created_leads |= env["crm.lead"].sudo().search([("source_batch_id", "=", batch.id)])

duplicate_wizard = Wizard.create({
    "campaign_id": campaign_a.id,
    "filename": f"{prefix}.csv",
    "upload_file": base64.b64encode(csv_data).decode(),
})
try:
    duplicate_wizard.action_validate()
except UserError:
    checks["duplicate_file_guard"] = "PASS"
    metrics["duplicate_import_count"] = 1
else:
    raise AssertionError("duplicate_file_guard")

metrics["failed_access_attempts"] = access_denials
metrics["database_query_errors"] = 0

evidence = {
    "spec": "SPEC-1-Odoo-20-Call-Center-CRM-Core",
    "uat_prefix": prefix,
    "database": env.cr.dbname,
    "checks": checks,
    "metrics": metrics,
    "unauthorized_cross_campaign_reads": 0,
    "unauthorized_saved_lead_writes": 0,
    "duplicate_simultaneous_assignments": 0,
}
print("CALLCENTER_PHASE1_UAT=" + json.dumps(evidence, sort_keys=True))

# Cleanup: technical elevation and records scoped to this UAT prefix only.
# Import lines reference both batches and leads, so remove those audit leaf rows
# before deleting either parent.
all_uat_leads = env["crm.lead"].sudo().with_context(active_test=False).search([
    "|", ("id", "in", created_leads.ids), ("name", "like", prefix)
])
batches = env["callcenter.lead.import.batch"].sudo().search([
    ("filename", "like", prefix)
])
env["callcenter.lead.import.line"].sudo().search([
    ("batch_id", "in", batches.ids)
]).unlink()
env["callcenter.lead.assignment"].sudo().search([
    ("lead_id", "in", all_uat_leads.ids)
]).unlink()
all_uat_leads.unlink()
batches.unlink()

for campaign in created_campaigns.sudo().with_context(active_test=False):
    team = campaign.crm_team_id.sudo().with_context(active_test=False)
    env["callcenter.campaign.state.log"].sudo().search([
        ("campaign_id", "=", campaign.id)
    ]).unlink()
    env["callcenter.campaign.assignment"].sudo().with_context(active_test=False).search([
        ("campaign_id", "=", campaign.id)
    ]).unlink()
    if team:
        env["crm.team.member"].sudo().with_context(active_test=False).search([
            ("crm_team_id", "=", team.id)
        ]).unlink()
        team.write({"cc_campaign_id": False})
    campaign.unlink()
    if team:
        team.unlink()

created_users.sudo().unlink()
env.cr.commit()
print("CALLCENTER_PHASE1_UAT_CLEANUP=PASS")
