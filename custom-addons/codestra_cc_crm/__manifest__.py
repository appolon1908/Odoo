{
    "name": "Codestra Contact Center CRM",
    "summary": "Campaign customer profiles, CRM ownership, and safe activities",
    "version": "19.0.1.1.1",
    "author": "Codestra",
    "license": "LGPL-3",
    "depends": [
        "codestra_cc_identity",
        "codestra_cc_mail",
        "call_center_campaign",
        "codestra_campaign_crm_os",
        "crm",
        "mail",
    ],
    "data": [
        "security/crm_security.xml",
        "security/ir.model.access.csv",
        "views/crm_workspace_views.xml",
    ],
    "installable": True,
    "application": True,
}
