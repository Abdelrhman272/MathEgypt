# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ExportShipment(models.Model):
    _name = "export.shipment"
    _description = "Export Shipment / Container"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(string="Shipment No", required=True, copy=False, default="New", tracking=True)
    company_id = fields.Many2one(
        "res.company", string="Company", required=True, default=lambda self: self.env.company, tracking=True
    )
    partner_id = fields.Many2one("res.partner", string="Customer", required=True, tracking=True)
    destination = fields.Char(string="Destination", tracking=True)

    container_no = fields.Char(string="Container No", tracking=True)
    seal_no = fields.Char(string="Seal No", tracking=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Reserved"),
            ("shipped", "Shipped"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
        required=True,
    )

    line_ids = fields.One2many("export.shipment.line", "shipment_id", string="Lines", copy=True)
    lot_line_ids = fields.One2many("export.shipment.lot", "shipment_id", string="Reserved Lots", copy=True)

    reserved_picking_id = fields.Many2one(
        "stock.picking", string="Reservation Picking", readonly=True, copy=False
    )
    sale_order_id = fields.Many2one("sale.order", string="Sale Order", readonly=True, copy=False)

    total_qty = fields.Float(string="Total Qty", compute="_compute_total_qty", store=True)

    @api.depends("line_ids.product_uom_qty")
    def _compute_total_qty(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_uom_qty"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name") in (False, "New", _("New")):
                vals["name"] = self.env["ir.sequence"].next_by_code("export.shipment") or "New"
        return super().create(vals_list)

    def _get_default_warehouse(self):
        self.ensure_one()
        wh = self.env["stock.warehouse"].search([("company_id", "=", self.company_id.id)], limit=1)
        if not wh:
            wh = self.env["stock.warehouse"].search([], limit=1)
        if not wh:
            raise UserError(_("No warehouse found to create reservation picking."))
        return wh

    def _validate_ready_to_reserve(self):
        self.ensure_one()
        if self.state not in ("draft",):
            raise UserError(_("Reservation is allowed only in Draft state."))
        if not self.line_ids:
            raise UserError(_("Add shipment lines first."))
        if not self.lot_line_ids:
            raise UserError(_("Add reserved lots first."))

        # Basic consistency: each reserved lot must belong to a line
        for lot_line in self.lot_line_ids:
            if not lot_line.line_id:
                raise UserError(_("Each reserved lot must be linked to a shipment line."))
            if lot_line.qty <= 0:
                raise UserError(_("Reserved quantity must be greater than zero."))

    def action_reserve_lots(self):
        """Create an outgoing picking and reserve the selected lots.

        Important (Odoo 19):
        - Do NOT write reserved quantities directly on stock.move.line.
        - We create stock.moves with demand quantities, confirm the picking,
          then reserve specific lots via move._update_reserved_quantity().
        """
        for rec in self:
            rec._validate_ready_to_reserve()

            if rec.reserved_picking_id:
                raise UserError(_("Reservation picking already exists."))

            wh = rec._get_default_warehouse()
            picking_type = wh.out_type_id
            if not picking_type:
                raise UserError(_("Outgoing picking type is not configured on the warehouse."))

            location_src = picking_type.default_location_src_id or wh.lot_stock_id
            location_dest = picking_type.default_location_dest_id or rec.partner_id.property_stock_customer
            if not location_src or not location_dest:
                raise UserError(_("Source/Destination locations are not properly configured."))

            picking = self.env["stock.picking"].create(
                {
                    "picking_type_id": picking_type.id,
                    "company_id": rec.company_id.id,
                    "partner_id": rec.partner_id.id,
                    "origin": rec.name,
                    "location_id": location_src.id,
                    "location_dest_id": location_dest.id,
                    "move_type": "direct",
                }
            )

            # One move per product/uom (demand = sum of reserved lots)
            moves_by_key = {}
            for lot_line in rec.lot_line_ids:
                key = (lot_line.product_id.id, lot_line.product_uom_id.id)
                moves_by_key.setdefault(key, 0.0)
                moves_by_key[key] += lot_line.qty

            move_records = {}
            for (product_id, uom_id), demand_qty in moves_by_key.items():
                move = self.env["stock.move"].create(
                    {
                        # In Odoo 19 the 'name' field is no longer accepted on stock.move create.
                        # The move description will be derived from the product / picking.
                        "picking_id": picking.id,
                        "company_id": rec.company_id.id,
                        "product_id": product_id,
                        "product_uom_qty": demand_qty,
                        "product_uom": uom_id,
                        "location_id": location_src.id,
                        "location_dest_id": location_dest.id,
                    }
                )
                move_records[(product_id, uom_id)] = move

            # Confirm first to allow reservation
            picking.action_confirm()

            # Reserve exact lots
            for lot_line in rec.lot_line_ids:
                move = move_records.get((lot_line.product_id.id, lot_line.product_uom_id.id))
                if not move:
                    continue
                # Reserve from source location with strict lot reservation
                move._update_reserved_quantity(
                    lot_line.qty,
                    location_src,
                    lot_id=lot_line.lot_id,
                    strict=True,
                )

            rec.reserved_picking_id = picking.id
            rec.state = "reserved"

    def action_unreserve(self):
        for rec in self:
            if not rec.reserved_picking_id:
                raise UserError(_("No reservation picking to unreserve."))
            if rec.state != "reserved":
                raise UserError(_("Unreserve is allowed only in Reserved state."))

            picking = rec.reserved_picking_id
            # Cancel picking to release reservation
            if picking.state not in ("cancel", "done"):
                picking.action_cancel()
            rec.reserved_picking_id = False
            rec.state = "draft"

    def action_create_sale_order(self):
        """Create a Sale Order for booking/invoicing purposes.
        For the demo: we link the SO to the shipment and keep the reservation picking as the delivery basis.
        """
        for rec in self:
            if rec.sale_order_id:
                raise UserError(_("Sale Order already created."))

            if not rec.partner_id:
                raise UserError(_("Set the customer first."))

            so = self.env["sale.order"].create(
                {
                    "partner_id": rec.partner_id.id,
                    "company_id": rec.company_id.id,
                    "origin": rec.name,
                }
            )
            for line in rec.line_ids:
                self.env["sale.order.line"].create(
                    {
                        "order_id": so.id,
                        "product_id": line.product_id.id,
                        "product_uom_qty": line.product_uom_qty,
                        "product_uom": line.product_uom_id.id,
                        "name": line.product_id.display_name,
                    }
                )

            rec.sale_order_id = so.id

    def action_validate_shipment(self):
        """Validate the reservation picking (ship).
        """
        for rec in self:
            if not rec.reserved_picking_id:
                raise UserError(_("Reserve lots first."))
            picking = rec.reserved_picking_id
            if picking.state == "done":
                rec.state = "shipped"
                continue
            if picking.state == "cancel":
                raise UserError(_("Reservation picking is cancelled. Create a new reservation."))

            # For demo: set qty_done = reserved for all move lines then validate
            for ml in picking.move_line_ids:
                if ml.qty_done == 0 and ml.reserved_uom_qty:
                    ml.qty_done = ml.reserved_uom_qty
            picking.button_validate()
            rec.state = "shipped"

    def action_cancel(self):
        for rec in self:
            if rec.reserved_picking_id and rec.reserved_picking_id.state not in ("cancel", "done"):
                rec.reserved_picking_id.action_cancel()
            rec.state = "cancel"

    def action_reset_to_draft(self):
        self.write({"state": "draft"})


class ExportShipmentLine(models.Model):
    _name = "export.shipment.line"
    _description = "Export Shipment Line"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Product/Grade", required=True)
    bom_id = fields.Many2one("mrp.bom", string="BOM", help="Auto-selected based on the product.")
    product_uom_id = fields.Many2one(
        "uom.uom", string="UoM", required=True, default=lambda self: self.env.ref("uom.product_uom_unit").id
    )
    product_uom_qty = fields.Float(string="Quantity", required=True, default=1.0)

    @api.onchange("product_id")
    def _onchange_product_id(self):
        for rec in self:
            if rec.product_id:
                rec.product_uom_id = rec.product_id.uom_id
                # Auto-pick BOM (company-specific first)
                bom = self.env["mrp.bom"]._bom_find(rec.product_id, company_id=rec.shipment_id.company_id.id if rec.shipment_id else False)
                rec.bom_id = bom.id if bom else False


class ExportShipmentLot(models.Model):
    _name = "export.shipment.lot"
    _description = "Export Shipment Reserved Lot"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    line_id = fields.Many2one("export.shipment.line", string="Related Line", required=True, ondelete="cascade")

    product_id = fields.Many2one(related="line_id.product_id", store=True, readonly=True)
    product_uom_id = fields.Many2one(related="line_id.product_uom_id", store=True, readonly=True)

    lot_id = fields.Many2one("stock.lot", string="Lot", required=True, domain="[('product_id', '=', product_id)]")
    qty = fields.Float(string="Reserved Qty", required=True, default=0.0)

    @api.onchange("line_id")
    def _onchange_line_id(self):
        for rec in self:
            if rec.line_id:
                # Default reserve the line qty (can be adjusted)
                rec.qty = rec.line_id.product_uom_qty
