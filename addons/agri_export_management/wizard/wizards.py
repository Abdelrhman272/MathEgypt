# -*- coding: utf-8 -*-
from odoo import fields, models


class AgxReserveLotsWizard(models.TransientModel):
    _name = "agx.reserve.lots.wizard"
    _description = "Reserve Lots Wizard"
    shipment_id = fields.Many2one("agx.shipment", required=True)
    product_id = fields.Many2one("product.product")
    lot_id = fields.Many2one("stock.lot")
    available_qty = fields.Float(readonly=True)
    reserve_qty = fields.Float(required=True, default=1.0)
    def action_apply(self):
        self.ensure_one()
        self.env["agx.shipment.lot.line"].create({"shipment_id": self.shipment_id.id, "product_id": self.product_id.id, "lot_id": self.lot_id.id, "available_qty": self.available_qty, "reserved_qty": self.reserve_qty})
        return {"type": "ir.actions.act_window_close"}


class AgxImportCostsWizard(models.TransientModel):
    _name = "agx.import.costs.wizard"
    _description = "Import Shipment Costs Wizard"
    shipment_id = fields.Many2one("agx.shipment", required=True)
    vendor_bill_id = fields.Many2one("account.move", domain="[('move_type', '=', 'in_invoice')]")
    landed_cost_id = fields.Many2one("stock.landed.cost")
    def action_apply(self):
        return {"type": "ir.actions.act_window_close"}


class AgxTraceabilityWizard(models.TransientModel):
    _name = "agx.traceability.wizard"
    _description = "Traceability Wizard"
    shipment_id = fields.Many2one("agx.shipment")
    batch_id = fields.Many2one("agx.batch")
    lot_id = fields.Many2one("stock.lot")
    result_html = fields.Html(readonly=True)
    def action_generate(self):
        self.ensure_one()
        parts = []
        if self.shipment_id:
            parts.append(f"<p><strong>Shipment:</strong> {self.shipment_id.display_name}</p>")
        if self.batch_id:
            parts.append(f"<p><strong>Batch:</strong> {self.batch_id.display_name}</p>")
        if self.lot_id:
            parts.append(f"<p><strong>Lot:</strong> {self.lot_id.display_name}</p>")
        self.result_html = ''.join(parts) or '<p>No data selected.</p>'
        return {"type": "ir.actions.act_window", "res_model": "agx.traceability.wizard", "res_id": self.id, "view_mode": "form", "target": "new"}
