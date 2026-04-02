# -*- coding: utf-8 -*-
{
    "name": "Agricultural Export Management",
    "summary": "End-to-end agricultural export management for Odoo 19",
    "version": "19.0.1.0.0",
    "category": "Inventory/Inventory",
    "author": "OpenAI / NextGen Systems",
    "license": "LGPL-3",
    "depends": ["base", "mail", "purchase", "stock", "mrp", "sale_management", "account", "base_setup"],
    "data": [
        "security/agx_security.xml",
        "security/ir.model.access.csv",
        "data/agx_sequence.xml",
        "views/dashboard_views.xml",
        "views/menu.xml",
        "views/views.xml",
        "views/settings_views.xml",
        "wizard/wizard_views.xml",
        "report/agx_reports.xml",
    ],
    "application": True,
    "installable": True,
}
