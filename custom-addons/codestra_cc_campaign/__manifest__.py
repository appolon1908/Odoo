{
    "name": "Codestra Contact Center Campaign",
    "summary": "Governed native CRM team campaign lifecycle and compatibility facade",
    "version": "19.0.2.0.0",
    "author": "Codestra",
    "license": "LGPL-3",
    "depends": [
        "codestra_cc_core",
        "codestra_cc_security",
        "codestra_vicidial_crm",
        "call_center_campaign",
        "codestra_campaign_crm_os",
        "codestra_staging_campaign_design",
    ],
    "data": [
        "security/campaign_lifecycle_security.xml",
        "security/ir.model.access.csv",
        "views/campaign_lifecycle_views.xml",
    ],
    "installable": True,
    "application": False,
}
