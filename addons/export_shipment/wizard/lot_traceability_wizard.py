# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ExportLotTraceabilityWizard(models.TransientModel):
    _name = "export.lot.traceability.wizard"
    _description = "Lot Traceability Wizard"

    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, readonly=True)
    lot_ids = fields.Many2many("stock.lot", string="Lots/Serials", help="Select lots you want to trace.")
    move_line_count = fields.Integer(string="Moves Count", compute="_compute_move_line_count")

    @api.depends("lot_ids")
    def _compute_move_line_count(self):
        StockMoveLine = self.env["stock.move.line"]
        for wiz in self:
            wiz.move_line_count = StockMoveLine.search_count([("lot_id", "in", wiz.lot_ids.ids)]) if wiz.lot_ids else 0

    @api.model
    def default_get(self, fields_list):
        res = super().default_get(fields_list)
        invoice_id = self.env.context.get("default_invoice_id")
        if not invoice_id:
            return res

        inv = self.env["account.move"].browse(invoice_id).exists()
        if not inv:
            return res

        res["invoice_id"] = inv.id

        sale_orders = self.env["sale.order"]
        if inv.invoice_origin:
            sale_orders = self.env["sale.order"].search([("name", "=", inv.invoice_origin)])
        if not sale_orders and inv.line_ids:
            sale_orders = inv.line_ids.sale_line_ids.order_id

        pickings = sale_orders.picking_ids.filtered(lambda p: p.state in ("assigned", "done"))
        done_pickings = pickings.filtered(lambda p: p.state == "done")
        if done_pickings:
            pickings = done_pickings

        lots = pickings.move_line_ids.mapped("lot_id").filtered(lambda l: l)
        res["lot_ids"] = [(6, 0, lots.ids)]
        return res

    # ---------------------------------------------------------------------
    # Helpers: unified trace lines
    # ---------------------------------------------------------------------
    def _get_trace_move_lines(self):
        """Return stock.move.line for selected lots, ordered by date."""
        self.ensure_one()
        if not self.lot_ids:
            return self.env["stock.move.line"]

        mls = self.env["stock.move.line"].search(
            [("lot_id", "in", self.lot_ids.ids)],
            order="date asc, id asc"
        )
        return mls

    def _format_picking_type(self, picking):
        if not picking:
            return ""
        pt = picking.picking_type_id
        if not pt:
            return ""
        # incoming/outgoing/internal
        code = getattr(pt, "code", "") or ""
        return code

    # ---------------------------------------------------------------------
    # Actions
    # ---------------------------------------------------------------------
    def action_open_traceability(self):
        self.ensure_one()
        if not self.lot_ids:
            raise UserError(_("No lots found/selected for this invoice."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Lot Traceability (Move Lines)"),
            "res_model": "stock.move.line",
            "view_mode": "tree,form,pivot,graph",
            "domain": [("lot_id", "in", self.lot_ids.ids)],
            "context": {
                "search_default_groupby_lot_id": 1,
                "search_default_groupby_picking_id": 1,
            },
        }

    def action_print_pdf(self):
        """Download PDF report."""
        self.ensure_one()
        if not self.lot_ids:
            raise UserError(_("No lots found/selected to print."))
        return self.env.ref("export_shipment.action_report_lot_traceability_pdf").report_action(self)

    def action_download_excel(self):
        """Download Excel via controller."""
        self.ensure_one()
        if not self.lot_ids:
            raise UserError(_("No lots found/selected to export."))
        return {
            "type": "ir.actions.act_url",
            "url": f"/export_shipment/traceability/xlsx/{self.id}",
            "target": "self",
        }
