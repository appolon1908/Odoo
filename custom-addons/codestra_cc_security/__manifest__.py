{
    "name": "Codestra Contact Center Security",
    "summary": "Fail-closed campaign membership and authorization controls",
    "version": "19.0.1.1.0",
    "author": "Codestra",
    "license": "LGPL-3",
    "depends": ["codestra_cc_core"],
    "data": [
        "security/groups.xml",
        "security/ir.model.access.csv",
        "security/record_rules.xml",
        "views/security_views.xml",
    ],
    "installable": True,
    "application": False,
}