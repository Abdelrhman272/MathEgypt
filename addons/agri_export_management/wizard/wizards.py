# -*- coding: utf-8 -*-
from markupsafe import escape as html_escape

from odoo import _, fields, models
from odoo.exceptions import UserError


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
        if not self.shipment_id or not self.product_id or not self.lot_id:
            raise UserError(_("Please select a shipment, product, and lot before applying the reservation."))
        effective_available = self.shipment_id._get_lot_effective_available_qty(
            lot_id=self.lot_id.id,
            product_id=self.product_id.id,
        )
        if self.reserve_qty <= 0:
            raise UserError(_("Reserve quantity must be greater than zero."))
        if self.reserve_qty > effective_available:
            raise UserError(_("Reserve quantity cannot exceed the currently available quantity for the selected lot."))
        batch_output = self.env["agx.batch.output"].search([
            ("lot_id", "=", self.lot_id.id),
            ("product_id", "=", self.product_id.id),
        ], limit=1)
        self.env["agx.shipment.lot.line"].create({
            "shipment_id": self.shipment_id.id,
            "product_id": self.product_id.id,
            "lot_id": self.lot_id.id,
            "batch_output_id": batch_output.id if batch_output else False,
            "available_qty": effective_available,
            "reserved_qty": self.reserve_qty,
        })
        return {"type": "ir.actions.act_window_close"}


class AgxImportCostsWizard(models.TransientModel):
    _name = "agx.import.costs.wizard"
    _description = "Import Shipment Costs Wizard"

    shipment_id = fields.Many2one("agx.shipment", required=True)
    vendor_bill_id = fields.Many2one("account.move", domain="[('move_type', '=', 'in_invoice')]")
    landed_cost_id = fields.Many2one("stock.landed.cost")

    def _get_default_cost_type(self):
        cost_type = self.env["agx.shipment.cost.type"].search([("code", "=", "other")], limit=1)
        if not cost_type:
            cost_type = self.env["agx.shipment.cost.type"].search([], limit=1)
        if not cost_type:
            cost_type = self.env["agx.shipment.cost.type"].create({
                "name": "Other",
                "code": "other",
                "default_allocation_basis": self.shipment_id.company_id.agx_default_logistics_basis or "qty",
            })
        return cost_type

    def action_apply(self):
        self.ensure_one()
        if not self.vendor_bill_id and not self.landed_cost_id:
            raise UserError(_("Please select a Vendor Bill or a Landed Cost to import."))
        cost_type = self._get_default_cost_type()
        allocation_basis = cost_type.default_allocation_basis or self.shipment_id.company_id.agx_default_logistics_basis or "qty"
        cost_lines_to_create = []

        if self.vendor_bill_id:
            invoice_lines = self.vendor_bill_id.invoice_line_ids.filtered(lambda l: not l.display_type)
            for bill_line in invoice_lines:
                already_linked = self.shipment_id.cost_line_ids.filtered(lambda l: l.vendor_bill_line_id == bill_line)
                if already_linked:
                    continue
                cost_lines_to_create.append({
                    "name": bill_line.name or bill_line.product_id.display_name or self.vendor_bill_id.display_name,
                    "cost_type_id": cost_type.id,
                    "allocation_basis": allocation_basis,
                    "cost_source": "vendor_bill_line",
                    "vendor_bill_id": self.vendor_bill_id.id,
                    "vendor_bill_line_id": bill_line.id,
                    "note": _("Imported from Vendor Bill %s") % (self.vendor_bill_id.display_name,),
                })
            if not invoice_lines:
                already_linked = self.shipment_id.cost_line_ids.filtered(lambda l: l.vendor_bill_id == self.vendor_bill_id and l.cost_source == "vendor_bill")
                if not already_linked:
                    cost_lines_to_create.append({
                        "name": self.vendor_bill_id.display_name,
                        "cost_type_id": cost_type.id,
                        "allocation_basis": allocation_basis,
                        "cost_source": "vendor_bill",
                        "vendor_bill_id": self.vendor_bill_id.id,
                        "note": _("Imported from Vendor Bill %s") % (self.vendor_bill_id.display_name,),
                    })

        if self.landed_cost_id:
            already_linked = self.shipment_id.cost_line_ids.filtered(lambda l: l.landed_cost_id == self.landed_cost_id)
            if not already_linked:
                cost_lines_to_create.append({
                    "name": self.landed_cost_id.display_name,
                    "cost_type_id": cost_type.id,
                    "allocation_basis": allocation_basis,
                    "cost_source": "landed_cost",
                    "landed_cost_id": self.landed_cost_id.id,
                    "note": _("Imported from Landed Cost %s") % (self.landed_cost_id.display_name,),
                })

        if cost_lines_to_create:
            self.shipment_id.write({"cost_line_ids": [(0, 0, vals) for vals in cost_lines_to_create]})
        return {"type": "ir.actions.act_window_close"}


class AgxTraceabilityWizard(models.TransientModel):
    _name = "agx.traceability.wizard"
    _description = "Traceability Wizard"

    shipment_id = fields.Many2one("agx.shipment")
    batch_id = fields.Many2one("agx.batch")
    lot_id = fields.Many2one("stock.lot")
    result_html = fields.Html(readonly=True)

    def _fmt(self, value):
        return html_escape(str(value or "-"))

    def _render_shipment_block(self, shipment):
        parts = [f"<h3>Shipment: {self._fmt(shipment.display_name)}</h3>"]
        parts.append("<ul>")
        parts.append(f"<li>Customer: {self._fmt(shipment.customer_id.display_name)}</li>")
        parts.append(f"<li>Destination: {self._fmt(shipment.destination_id.display_name)}</li>")
        parts.append(f"<li>Containers: {self._fmt(shipment.container_count)}</li>")
        parts.append(f"<li>Total Reserved Qty: {self._fmt(shipment.total_reserved_qty)}</li>")
        parts.append("</ul>")
        if shipment.lot_line_ids:
            parts.append("<h4>Reserved Lots</h4><table class='table table-sm table-bordered'><thead><tr><th>Product</th><th>Lot</th><th>Reserved Qty</th><th>Batch</th><th>Evaluation</th></tr></thead><tbody>")
            for lot_line in shipment.lot_line_ids:
                batch = lot_line.batch_output_id.batch_id
                evaluation = batch.evaluation_id if batch else False
                parts.append(
                    f"<tr><td>{self._fmt(lot_line.product_id.display_name)}</td>"
                    f"<td>{self._fmt(lot_line.lot_id.display_name)}</td>"
                    f"<td>{self._fmt(lot_line.reserved_qty)}</td>"
                    f"<td>{self._fmt(batch.display_name if batch else '')}</td>"
                    f"<td>{self._fmt(evaluation.display_name if evaluation else '')}</td></tr>"
                )
            parts.append("</tbody></table>")
        return "".join(parts)

    def _render_batch_block(self, batch):
        parts = [f"<h3>Batch: {self._fmt(batch.display_name)}</h3>"]
        parts.append("<ul>")
        parts.append(f"<li>Evaluation: {self._fmt(batch.evaluation_id.display_name)}</li>")
        parts.append(f"<li>Input Qty: {self._fmt(batch.input_qty)}</li>")
        parts.append(f"<li>Output Qty: {self._fmt(batch.output_qty)}</li>")
        parts.append(f"<li>Allocable Cost: {self._fmt(batch.effective_allocable_cost)}</li>")
        parts.append("</ul>")
        if batch.input_line_ids:
            parts.append("<h4>Inputs</h4><table class='table table-sm table-bordered'><thead><tr><th>Product</th><th>Lot</th><th>Qty</th></tr></thead><tbody>")
            for line in batch.input_line_ids:
                parts.append(
                    f"<tr><td>{self._fmt(line.product_id.display_name)}</td>"
                    f"<td>{self._fmt(line.lot_id.display_name)}</td>"
                    f"<td>{self._fmt(line.qty)}</td></tr>"
                )
            parts.append("</tbody></table>")
        if batch.output_line_ids:
            parts.append("<h4>Outputs</h4><table class='table table-sm table-bordered'><thead><tr><th>Product</th><th>Lot</th><th>Grade</th><th>Size</th><th>Qty</th></tr></thead><tbody>")
            for line in batch.output_line_ids:
                parts.append(
                    f"<tr><td>{self._fmt(line.product_id.display_name)}</td>"
                    f"<td>{self._fmt(line.lot_id.display_name)}</td>"
                    f"<td>{self._fmt(line.grade_id.display_name)}</td>"
                    f"<td>{self._fmt(line.size_id.display_name)}</td>"
                    f"<td>{self._fmt(line.qty)}</td></tr>"
                )
            parts.append("</tbody></table>")
        return "".join(parts)

    def _render_lot_block(self, lot):
        batch_outputs = self.env["agx.batch.output"].search([("lot_id", "=", lot.id)])
        consuming_batches = self.env["agx.batch.input"].search([("lot_id", "=", lot.id)]).mapped("batch_id")
        shipment_lots = self.env["agx.shipment.lot.line"].search([("lot_id", "=", lot.id), ("shipment_id.state", "!=", "cancelled")])
        quants = self.env["stock.quant"].search([("lot_id", "=", lot.id), ("location_id.usage", "=", "internal")])
        on_hand_qty = sum(quants.mapped("quantity"))

        parts = [f"<h3>Lot: {self._fmt(lot.display_name)}</h3>"]
        parts.append("<ul>")
        parts.append(f"<li>Product: {self._fmt(lot.product_id.display_name)}</li>")
        parts.append(f"<li>Internal On Hand Qty: {self._fmt(on_hand_qty)}</li>")
        parts.append("</ul>")

        if batch_outputs:
            parts.append("<h4>Produced In</h4><table class='table table-sm table-bordered'><thead><tr><th>Batch</th><th>Product</th><th>Grade</th><th>Size</th><th>Qty</th></tr></thead><tbody>")
            for output in batch_outputs:
                parts.append(
                    f"<tr><td>{self._fmt(output.batch_id.display_name)}</td>"
                    f"<td>{self._fmt(output.product_id.display_name)}</td>"
                    f"<td>{self._fmt(output.grade_id.display_name)}</td>"
                    f"<td>{self._fmt(output.size_id.display_name)}</td>"
                    f"<td>{self._fmt(output.qty)}</td></tr>"
                )
            parts.append("</tbody></table>")

        if consuming_batches:
            parts.append("<h4>Consumed In</h4><ul>")
            for batch in consuming_batches:
                parts.append(f"<li>{self._fmt(batch.display_name)}</li>")
            parts.append("</ul>")

        if shipment_lots:
            parts.append("<h4>Reserved In Shipments</h4><table class='table table-sm table-bordered'><thead><tr><th>Shipment</th><th>Product</th><th>Reserved Qty</th></tr></thead><tbody>")
            for line in shipment_lots:
                parts.append(
                    f"<tr><td>{self._fmt(line.shipment_id.display_name)}</td>"
                    f"<td>{self._fmt(line.product_id.display_name)}</td>"
                    f"<td>{self._fmt(line.reserved_qty)}</td></tr>"
                )
            parts.append("</tbody></table>")
        return "".join(parts)

    def action_generate(self):
        self.ensure_one()
        parts = []
        if self.shipment_id:
            parts.append(self._render_shipment_block(self.shipment_id))
        if self.batch_id:
            parts.append(self._render_batch_block(self.batch_id))
        if self.lot_id:
            parts.append(self._render_lot_block(self.lot_id))
        self.result_html = "".join(parts) or "<p>No data selected.</p>"
        return {
            "type": "ir.actions.act_window",
            "res_model": "agx.traceability.wizard",
            "res_id": self.id,
            "view_mode": "form",
            "target": "new",
        }
