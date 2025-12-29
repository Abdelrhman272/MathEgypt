# -*- coding: utf-8 -*-

from odoo import api, fields, models, _
from odoo.exceptions import ValidationError


class ExportShipment(models.Model):
    _name = "export.shipment"
    _description = "Export Shipment"
    _inherit = ["mail.thread", "mail.activity.mixin"]

    name = fields.Char(string="Shipment No", required=True, copy=False, readonly=True, default="New", tracking=True)
    company_id = fields.Many2one("res.company", default=lambda self: self.env.company, required=True)
    partner_id = fields.Many2one("res.partner", string="Customer", required=True)
    destination = fields.Char(string="Destination")
    container_no = fields.Char(string="Container No", required=True)
    seal_no = fields.Char(string="Seal No")

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Reserved"),
            ("done", "Shipped"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    line_ids = fields.One2many("export.shipment.line", "shipment_id", string="Lines")
    lot_line_ids = fields.One2many("export.shipment.lot.line", "shipment_id", string="Reserved Lots")

    sale_order_id = fields.Many2one("sale.order", string="Sale Order", readonly=True, copy=False)
    picking_id = fields.Many2one("stock.picking", string="Reservation Picking", readonly=True, copy=False)

    total_qty = fields.Float(compute="_compute_total_qty", store=True)

    @api.depends("line_ids.product_uom_qty")
    def _compute_total_qty(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_uom_qty"))

    # -------------------------
    # SEQUENCE (Fix "New" issue)
    # -------------------------
    def _ensure_sequence_exists(self):
        """
        Ensures an ir.sequence exists for export.shipment even if sequence.xml is not loaded.
        This fixes the case where 'Shipment No' stays 'New'.
        """
        seq_model = self.env["ir.sequence"].sudo()
        code = "export.shipment"
        seq = seq_model.search([("code", "=", code)], limit=1)
        if not seq:
            # Create a global sequence (no company_id) so it works across companies
            seq_model.create(
                {
                    "name": "Export Shipment Sequence",
                    "code": code,
                    "prefix": "EXP/%(year)s/",
                    "padding": 5,
                    "implementation": "standard",
                    "active": True,
                    "company_id": False,
                }
            )

    def _next_shipment_name(self):
        self._ensure_sequence_exists()
        return self.env["ir.sequence"].next_by_code("export.shipment") or "New"

    @api.model
    def default_get(self, fields_list):
        defaults = super().default_get(fields_list)
        # Odoo may inject default="New", we replace it with a sequence number
        if "name" in fields_list and (not defaults.get("name") or defaults.get("name") == "New"):
            defaults["name"] = self._next_shipment_name()
        return defaults

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            # If created via RPC/import and name remains New, generate it
            if not vals.get("name") or vals.get("name") == "New":
                vals["name"] = self._next_shipment_name()
        return super().create(vals_list)

    # -------------------------
    # Actions
    # -------------------------
    def action_mark_reserved(self):
        for rec in self:
            if not rec.line_ids:
                raise ValidationError(_("Please add at least one line."))

            # Create one picking to reserve all moves (from stock to customer location later)
            picking_type = self.env.ref("stock.picking_type_out", raise_if_not_found=False)
            if not picking_type:
                # fallback: any outgoing type
                picking_type = self.env["stock.picking.type"].search([("code", "=", "outgoing")], limit=1)

            if not picking_type:
                raise ValidationError(_("No outgoing picking type found."))

            picking = self.env["stock.picking"].create(
                {
                    "picking_type_id": picking_type.id,
                    "location_id": picking_type.default_location_src_id.id,
                    "location_dest_id": self.env.ref("stock.stock_location_customers").id,
                    "origin": rec.name,
                    "company_id": rec.company_id.id,
                    "partner_id": rec.partner_id.id,
                }
            )

            moves = []
            for line in rec.line_ids:
                if line.product_uom_qty <= 0:
                    continue
                move_vals = {
                    "picking_id": picking.id,
                    "name": line.product_id.display_name,
                    "product_id": line.product_id.id,
                    "product_uom": line.product_uom_id.id,
                    "product_uom_qty": line.product_uom_qty,
                    "location_id": picking.location_id.id,
                    "location_dest_id": picking.location_dest_id.id,
                    "company_id": rec.company_id.id,
                }
                moves.append((0, 0, move_vals))

            if not moves:
                raise ValidationError(_("All lines have zero quantity."))

            picking.write({"move_ids_without_package": moves})
            picking.action_confirm()
            picking.action_assign()

            rec.picking_id = picking.id
            rec.state = "reserved"

            # Fill reserved lots lines automatically based on reserved move lines
            rec._sync_reserved_lots_from_picking()

    def _sync_reserved_lots_from_picking(self):
        """
        Create/update lot_line_ids based on the reserved move lines in the picking.
        User can then edit only the lot_id if needed.
        """
        for rec in self:
            if not rec.picking_id:
                continue

            rec.lot_line_ids.unlink()

            for ml in rec.picking_id.move_line_ids:
                # reserved quantity on move line is qty_done=0 and qty_reserved depends on Odoo version,
                # but at least we keep expected qty in product_uom_qty.
                qty = ml.product_uom_qty or 0.0
                if qty <= 0:
                    continue

                related_line = rec.line_ids.filtered(lambda l: l.product_id == ml.product_id)[:1]
                self.env["export.shipment.lot.line"].create(
                    {
                        "shipment_id": rec.id,
                        "line_id": related_line.id if related_line else False,
                        "product_id": ml.product_id.id,
                        "lot_id": ml.lot_id.id if ml.lot_id else False,
                        "qty": qty,
                    }
                )

    def action_validate_shipment(self):
        for rec in self:
            if rec.state != "reserved":
                raise ValidationError(_("Shipment must be in Reserved state to validate."))

            if not rec.picking_id:
                raise ValidationError(_("No reservation picking found."))

            # Apply chosen lots to picking move lines
            # We'll distribute quantities by product (simple approach)
            lots_by_product = {}
            for ll in rec.lot_line_ids:
                if ll.qty <= 0:
                    continue
                lots_by_product.setdefault(ll.product_id.id, []).append((ll.lot_id.id, ll.qty))

            # Reset existing move lines lots then set according to lot lines
            for move in rec.picking_id.move_ids:
                # remove existing move lines; recreate with lot allocation
                move.move_line_ids.unlink()

                allocations = lots_by_product.get(move.product_id.id, [])
                if not allocations:
                    # If no lots provided, keep single line without lot (only valid if product not tracked)
                    self.env["stock.move.line"].create(
                        {
                            "move_id": move.id,
                            "picking_id": rec.picking_id.id,
                            "product_id": move.product_id.id,
                            "product_uom_id": move.product_uom.id,
                            "location_id": move.location_id.id,
                            "location_dest_id": move.location_dest_id.id,
                            "product_uom_qty": move.product_uom_qty,
                            "qty_done": move.product_uom_qty,
                        }
                    )
                    continue

                total_alloc = sum(q for _, q in allocations)
                if total_alloc != move.product_uom_qty:
                    raise ValidationError(
                        _(
                            "Allocated lots quantity (%s) for product %s does not match required quantity (%s)."
                        )
                        % (total_alloc, move.product_id.display_name, move.product_uom_qty)
                    )

                for lot_id, qty in allocations:
                    self.env["stock.move.line"].create(
                        {
                            "move_id": move.id,
                            "picking_id": rec.picking_id.id,
                            "product_id": move.product_id.id,
                            "product_uom_id": move.product_uom.id,
                            "location_id": move.location_id.id,
                            "location_dest_id": move.location_dest_id.id,
                            "lot_id": lot_id,
                            "product_uom_qty": qty,
                            "qty_done": qty,
                        }
                    )

            rec.picking_id.button_validate()
            rec.state = "done"

    def action_cancel(self):
        for rec in self:
            if rec.picking_id and rec.picking_id.state not in ("done", "cancel"):
                rec.picking_id.action_cancel()
            rec.state = "cancel"

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = "draft"


class ExportShipmentLine(models.Model):
    _name = "export.shipment.line"
    _description = "Export Shipment Line"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", required=True)
    product_uom_qty = fields.Float(string="Quantity", required=True, default=1.0)
    product_uom_id = fields.Many2one("uom.uom", string="UoM", required=True)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.product_uom_id = rec.product_id.uom_id.id


class ExportShipmentLotLine(models.Model):
    _name = "export.shipment.lot.line"
    _description = "Export Shipment Lot Line"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    line_id = fields.Many2one("export.shipment.line", string="Related Line")
    product_id = fields.Many2one("product.product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Lot/Serial")
    qty = fields.Float(required=True, default=1.0)
