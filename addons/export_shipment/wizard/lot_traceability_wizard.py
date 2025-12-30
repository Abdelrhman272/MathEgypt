# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ExportLotTraceabilityWizard(models.TransientModel):
    """
    Wizard to build a full lot genealogy starting from an invoice (customer delivery)
    and going back to:
      - delivery picking (shipment / outgoing)
      - MRP productions that created those finished lots
      - raw lots consumed by those productions
      - incoming receipts/vendor for those raw lots (if exists)

    NOTE: We DO NOT ask the user to select lots.
    We auto-detect finished lots from:
      Invoice -> related Export Shipments -> Reservation Picking -> done move lines lots
    """
    _name = "export.lot.traceability.wizard"
    _description = "Export Lot Traceability Wizard"

    invoice_id = fields.Many2one("account.move", string="Invoice", required=True, readonly=True)
    shipment_ids = fields.Many2many("export.shipment", string="Related Shipments", readonly=True)
    lot_ids = fields.Many2many("stock.lot", string="Detected Finished Lots", readonly=True)
    line_ids = fields.One2many("export.lot.traceability.line", "wizard_id", string="Trace Lines")

    # Excel export (download)
    file_name = fields.Char(readonly=True)
    file_data = fields.Binary(readonly=True)

    @api.model
    def default_get(self, fields_list):
        """Pre-fill invoice from context + detect shipments/lots automatically."""
        res = super().default_get(fields_list)

        active_model = self.env.context.get("active_model")
        active_id = self.env.context.get("active_id")
        if active_model != "account.move" or not active_id:
            return res

        inv = self.env["account.move"].browse(active_id).exists()
        if not inv:
            return res

        res["invoice_id"] = inv.id

        shipments = self.env["export.shipment"].search([("sale_order_id", "=", inv.invoice_origin)], limit=50)
        # Fallback: if you stored export_shipment_id on sale.order you can map it; keep simple here.
        res["shipment_ids"] = [(6, 0, shipments.ids)]

        finished_lots = self._get_finished_lots_from_shipments(shipments)
        res["lot_ids"] = [(6, 0, finished_lots.ids)]

        return res

    # -------------------------------------------------------------------------
    # Helpers
    # -------------------------------------------------------------------------
    @api.model
    def _get_finished_lots_from_shipments(self, shipments):
        """Return lots shipped on the reservation pickings of shipments (done lines)."""
        lots = self.env["stock.lot"]
        for sh in shipments:
            picking = sh.reserved_picking_id
            if not picking:
                continue
            # Prefer done pickings, but allow non-done to preview
            mls = picking.move_line_ids.filtered(lambda l: l.lot_id)
            lots |= mls.mapped("lot_id")
        return lots

    def _get_delivery_move_lines_for_lot(self, lot):
        """Outgoing move lines where this lot was shipped (customer deliveries)."""
        self.ensure_one()
        MoveLine = self.env["stock.move.line"]
        return MoveLine.search([
            ("lot_id", "=", lot.id),
            ("state", "=", "done"),
            ("picking_id.picking_type_id.code", "=", "outgoing"),
        ], order="date desc")

    def _find_production_for_lot(self, lot, product):
        """
        Find the MRP production that produced this finished lot.
        We search stock.move.line for this lot on finished moves linked to a production.
        """
        self.ensure_one()
        MoveLine = self.env["stock.move.line"]
        ml = MoveLine.search([
            ("lot_id", "=", lot.id),
            ("product_id", "=", product.id),
            ("state", "=", "done"),
            ("move_id.production_id", "!=", False),
        ], limit=1, order="date desc")
        return ml.move_id.production_id if ml else False

    def _get_raw_lots_from_production(self, production):
        """Return raw lots consumed by a production (from move_raw_ids move lines)."""
        self.ensure_one()
        raw_lots = self.env["stock.lot"]
        for mv in production.move_raw_ids.filtered(lambda m: m.state == "done"):
            raw_lots |= mv.move_line_ids.filtered(lambda l: l.lot_id).mapped("lot_id")
        return raw_lots

    def _find_vendor_receipt_for_lot(self, lot):
        """
        Find the incoming receipt (vendor) that brought this lot to stock.
        Returns tuple: (picking, partner, date)
        """
        self.ensure_one()
        MoveLine = self.env["stock.move.line"]
        ml = MoveLine.search([
            ("lot_id", "=", lot.id),
            ("state", "=", "done"),
            ("picking_id.picking_type_id.code", "=", "incoming"),
        ], limit=1, order="date asc")
        if not ml:
            return (False, False, False)
        picking = ml.picking_id
        return (picking, picking.partner_id, ml.date)

    def _trace_lot_chain(self, finished_lot):
        """
        Build a trace row dict for a finished lot:
        finished -> (production) -> raw lots -> (vendor receipts for each raw lot)
        """
        self.ensure_one()

        product = finished_lot.product_id
        production = self._find_production_for_lot(finished_lot, product) if product else False
        raw_lots = self._get_raw_lots_from_production(production) if production else self.env["stock.lot"]

        # Vendor data (many raw lots => list)
        vendor_names = []
        receipt_refs = []
        receipt_dates = []

        for raw_lot in raw_lots:
            receipt, vendor, rdate = self._find_vendor_receipt_for_lot(raw_lot)
            if vendor and vendor.display_name:
                vendor_names.append(vendor.display_name)
            if receipt and receipt.name:
                receipt_refs.append(receipt.name)
            if rdate:
                receipt_dates.append(rdate)

        return {
            "finished_product": product.display_name if product else "",
            "finished_lot": finished_lot.name or "",
            "production": production.name if production else "",
            "production_date": production.date_start if production else False,
            "raw_lots": ", ".join(raw_lots.mapped("name")) if raw_lots else "",
            "vendors": ", ".join(sorted(set(vendor_names))) if vendor_names else "",
            "receipts": ", ".join(sorted(set(receipt_refs))) if receipt_refs else "",
            "receipt_dates": receipt_dates and min(receipt_dates) or False,
        }

    # -------------------------------------------------------------------------
    # Actions
    # -------------------------------------------------------------------------
    def action_generate_traceability(self):
        """Generate transient lines for all detected finished lots."""
        for wiz in self:
            wiz.line_ids.unlink()

            if not wiz.invoice_id:
                raise UserError(_("No invoice found."))

            if not wiz.lot_ids:
                raise UserError(_("No lots detected for this invoice/shipment."))

            # Outgoing delivery info for the invoice customer (for context)
            customer = wiz.invoice_id.partner_id

            lines_vals = []
            for lot in wiz.lot_ids:
                chain = wiz._trace_lot_chain(lot)

                # Latest delivery for this lot
                delivery_mls = wiz._get_delivery_move_lines_for_lot(lot)
                delivery_picking = delivery_mls[:1].picking_id if delivery_mls else False
                delivery_date = delivery_mls[:1].date if delivery_mls else False

                lines_vals.append({
                    "wizard_id": wiz.id,
                    "invoice_id": wiz.invoice_id.id,
                    "customer_id": customer.id,
                    "shipment_refs": ", ".join(wiz.shipment_ids.mapped("name")) if wiz.shipment_ids else "",
                    "delivery_picking": delivery_picking.name if delivery_picking else "",
                    "delivery_date": delivery_date,
                    "finished_product": chain["finished_product"],
                    "finished_lot": chain["finished_lot"],
                    "production": chain["production"],
                    "production_date": chain["production_date"],
                    "raw_lots": chain["raw_lots"],
                    "vendors": chain["vendors"],
                    "receipts": chain["receipts"],
                    "receipt_date": chain["receipt_dates"],
                })

            self.env["export.lot.traceability.line"].create(lines_vals)

            # Show results on the wizard itself
            return {
                "type": "ir.actions.act_window",
                "name": _("Lot Traceability"),
                "res_model": "export.lot.traceability.wizard",
                "view_mode": "form",
                "res_id": wiz.id,
                "target": "new",
            }

    def action_open_screen(self):
        """Open a tree view of generated traceability lines."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Generate the traceability report first."))

        return {
            "type": "ir.actions.act_window",
            "name": _("Lot Traceability Lines"),
            "res_model": "export.lot.traceability.line",
            "view_mode": "tree",
            "target": "current",
            "domain": [("wizard_id", "=", self.id)],
            "context": {"default_wizard_id": self.id},
        }

    def action_print_pdf(self):
        """Print PDF report."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Generate the traceability report first."))
        return self.env.ref("export_shipment.action_report_lot_traceability").report_action(self)

    def action_export_excel(self):
        """Generate and attach an XLSX file for download."""
        self.ensure_one()
        if not self.line_ids:
            raise UserError(_("Generate the traceability report first."))

        import io
        import base64
        import xlsxwriter

        output = io.BytesIO()
        wb = xlsxwriter.Workbook(output, {"in_memory": True})
        ws = wb.add_worksheet("Traceability")

        headers = [
            "Invoice", "Customer", "Shipments", "Delivery Picking", "Delivery Date",
            "Finished Product", "Finished Lot", "Production", "Production Date",
            "Raw Lots", "Vendors", "Receipts", "Receipt Date",
        ]
        for col, h in enumerate(headers):
            ws.write(0, col, h)

        row = 1
        for ln in self.line_ids:
            ws.write(row, 0, ln.invoice_id.name or "")
            ws.write(row, 1, ln.customer_id.display_name or "")
            ws.write(row, 2, ln.shipment_refs or "")
            ws.write(row, 3, ln.delivery_picking or "")
            ws.write(row, 4, str(ln.delivery_date or ""))
            ws.write(row, 5, ln.finished_product or "")
            ws.write(row, 6, ln.finished_lot or "")
            ws.write(row, 7, ln.production or "")
            ws.write(row, 8, str(ln.production_date or ""))
            ws.write(row, 9, ln.raw_lots or "")
            ws.write(row, 10, ln.vendors or "")
            ws.write(row, 11, ln.receipts or "")
            ws.write(row, 12, str(ln.receipt_date or ""))
            row += 1

        wb.close()
        output.seek(0)

        self.file_name = f"lot_traceability_{(self.invoice_id.name or 'invoice')}.xlsx"
        self.file_data = base64.b64encode(output.read())
        output.close()

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

    wizard_id = fields.Many2one("export.lot.traceability.wizard", required=True, ondelete="cascade", index=True)

    invoice_id = fields.Many2one("account.move", readonly=True)
    customer_id = fields.Many2one("res.partner", readonly=True)

    shipment_refs = fields.Char(readonly=True)
    delivery_picking = fields.Char(readonly=True)
    delivery_date = fields.Datetime(readonly=True)

    finished_product = fields.Char(readonly=True)
    finished_lot = fields.Char(readonly=True)

    production = fields.Char(readonly=True)
    production_date = fields.Datetime(readonly=True)

    raw_lots = fields.Char(readonly=True)
    vendors = fields.Char(readonly=True)
    receipts = fields.Char(readonly=True)
    receipt_date = fields.Datetime(readonly=True)
