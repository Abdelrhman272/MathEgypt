# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class AccountMove(models.Model):
    _inherit = "account.move"

    def action_open_lot_traceability_wizard(self):
        self.ensure_one()
        if self.move_type not in ("out_invoice", "out_refund"):
            raise UserError(_("Traceability is available only for Customer Invoices / Refunds."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Traceability Report"),
            "res_model": "export.lot.traceability.wizard",
            "view_mode": "form",
            "target": "new",
            "context": {
                "default_invoice_id": self.id,
            },
        }
