# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ExportLotTraceabilityWizard(models.TransientModel):
    _name = "export.lot.traceability.wizard"
    _description = "Export Lot Traceability Wizard"

    invoice_id = fields.Many2one(
        "account.move",
        string="Invoice",
        required=True,
        readonly=True
    )

    shipment_ids = fields.Many2many(
        "export.shipment",
        string="Related Shipments",
        readonly=True
    )

    lot_ids = fields.Many2many(
        "stock.lot",
        string="Detected Finished Lots",
        readonly=True
    )

    line_ids = fields.One2many(
        "export.lot.traceability.line",
        "wizard_id",
        string="Traceability Lines"
    )

    file_name = fields.Char(readonly=True)
    file_data = fields.Binary(readonly=True)

    # --------------------------------------------------
    # Default Get
    # --------------------------------------------------
    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)

        if self.env.context.get("active_model") != "account.move":
            return res

        invoice = self.env["account.move"].browse(self.env.context.get("active_id"))
        if not invoice:
            return res

        res["invoice_id"] = invoice.id

        shipments = self.env["export.shipment"].search([
            ("sale_order_id", "=", invoice.invoice_origin)
        ])
        res["shipment_ids"] = [(6, 0, shipments.ids)]

        lots = self._get_finished_lots_from_shipments(shipments)
        res["lot_ids"] = [(6, 0, lots.ids)]

        return res

    # --------------------------------------------------
    # Helpers
    # --------------------------------------------------
    def _get_finished_lots_from_shipments(self, shipments):
        lots = self.env["stock.lot"]
        for sh in shipments:
            picking = sh.reserved_picking_id
            if picking:
                lots |= picking.move_line_ids.filtered(
                    lambda l: l.lot_id
                ).mapped("lot_id")
        return lots

    # --------------------------------------------------
    # Actions
    # --------------------------------------------------
    def action_generate_traceability(self):
        self.ensure_one()
        self.line_ids.unlink()

        if not self.lot_ids:
            raise UserError(_("No lots detected."))

        lines = []
        for lot in self.lot_ids:
            lines.append({
                "wizard_id": self.id,
                "finished_product": lot.product_id.display_name,
                "finished_lot": lot.name,
            })

        self.env["export.lot.traceability.line"].create(lines)

        return {
            "type": "ir.actions.act_window",
            "res_model": "export.lot.traceability.wizard",
            "view_mode": "form",
            "res_id": self.id,
            "target": "new",
        }


class ExportLotTraceabilityLine(models.TransientModel):
    _name = "export.lot.traceability.line"
    _description = "Export Lot Traceability Line"
    _order = "id"

    wizard_id = fields.Many2one(
        "export.lot.traceability.wizard",
        required=True,
        ondelete="cascade"
    )

    finished_product = fields.Char(readonly=True)
    finished_lot = fields.Char(readonly=True)
    production = fields.Char(readonly=True)
    production_date = fields.Datetime(readonly=True)
    raw_lots = fields.Char(readonly=True)
    vendors = fields.Char(readonly=True)
    receipts = fields.Char(readonly=True)
    receipt_date = fields.Datetime(readonly=True)
    delivery_date = fields.Datetime(readonly=True)
