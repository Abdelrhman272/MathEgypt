# -*- coding: utf-8 -*-
from odoo import api, fields, models, _
from odoo.exceptions import UserError


class ExportShipment(models.Model):
    _name = "export.shipment"
    _description = "Export Shipment / Container"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "id desc"

    name = fields.Char(
        string="Shipment No",
        required=True,
        copy=False,
        default=lambda self: _("New"),
        tracking=True,
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        required=True,
        default=lambda self: self.env.company,
        tracking=True,
    )

    partner_id = fields.Many2one(
        "res.partner",
        string="Customer",
        required=True,
        domain=[("customer_rank", ">", 0)],
        tracking=True,
    )

    container_no = fields.Char(string="Container No", tracking=True)
    seal_no = fields.Char(string="Seal No", tracking=True)
    destination = fields.Char(string="Destination", tracking=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Lots Reserved"),
            ("so_created", "Sales Created"),
            ("del_created", "Delivery Created"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    line_ids = fields.One2many(
        "export.shipment.line", "shipment_id", string="Shipment Lines", copy=True
    )

    lot_line_ids = fields.One2many(
        "export.shipment.lot", "shipment_id", string="Reserved Lots", copy=True
    )

    sale_order_id = fields.Many2one("sale.order", string="Sales Order", copy=False)
    picking_id = fields.Many2one("stock.picking", string="Delivery", copy=False)

    total_qty = fields.Float(string="Total Qty", compute="_compute_total_qty")

    @api.depends("line_ids.product_uom_qty")
    def _compute_total_qty(self):
        for rec in self:
            rec.total_qty = sum(rec.line_ids.mapped("product_uom_qty"))

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            if vals.get("name", _("New")) == _("New"):
                vals["name"] = self.env["ir.sequence"].next_by_code("export.shipment") or _("New")
        return super().create(vals_list)

    # -----------------------------
    # Actions
    # -----------------------------
    def action_mark_reserved(self):
        for rec in self:
            if not rec.lot_line_ids:
                raise UserError(_("Please add at least one reserved lot line before marking Reserved."))
            rec.state = "reserved"

    def action_create_sale_order(self):
        self.ensure_one()
        if self.sale_order_id:
            raise UserError(_("Sales Order already created."))
        if not self.partner_id:
            raise UserError(_("Please set Customer."))

        container_product = self.env["product.product"].search(
            [("name", "ilike", "Container Service")], limit=1
        )
        if not container_product:
            raise UserError(_("Create a product named 'Container Service' (Service) to generate the Sales Order."))

        so = self.env["sale.order"].create(
            {
                "partner_id": self.partner_id.id,
                "company_id": self.company_id.id,
                "origin": self.name,
            }
        )

        self.env["sale.order.line"].create(
            {
                "order_id": so.id,
                "product_id": container_product.id,
                "product_uom_qty": 1.0,
                "name": f"{container_product.display_name} - {self.container_no or ''}".strip(),
            }
        )

        self.sale_order_id = so.id
        self.state = "so_created"

        return {
            "type": "ir.actions.act_window",
            "name": _("Sales Order"),
            "res_model": "sale.order",
            "view_mode": "form",
            "res_id": so.id,
        }

    def action_create_delivery_placeholder(self):
        self.ensure_one()
        if self.picking_id:
            raise UserError(_("Delivery already created."))

        picking_type = self.env["stock.picking.type"].search(
            [("code", "=", "outgoing"), ("warehouse_id.company_id", "=", self.company_id.id)],
            limit=1,
        )
        if not picking_type:
            raise UserError(_("No outgoing picking type found for this company."))

        customer_location = self.partner_id.property_stock_customer
        src_location = picking_type.default_location_src_id
        if not src_location:
            raise UserError(_("Outgoing picking type has no default source location."))

        picking = self.env["stock.picking"].create(
            {
                "picking_type_id": picking_type.id,
                "location_id": src_location.id,
                "location_dest_id": customer_location.id,
                "partner_id": self.partner_id.id,
                "origin": self.name,
            }
        )

        self.picking_id = picking.id
        self.state = "del_created"

        return {
            "type": "ir.actions.act_window",
            "name": _("Delivery"),
            "res_model": "stock.picking",
            "view_mode": "form",
            "res_id": picking.id,
        }

    def action_cancel(self):
        for rec in self:
            rec.state = "cancel"

    def action_reset_to_draft(self):
        for rec in self:
            rec.state = "draft"


class ExportShipmentLine(models.Model):
    _name = "export.shipment.line"
    _description = "Export Shipment Line"
    _order = "id asc"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    product_id = fields.Many2one(
        "product.product",
        string="Product/Grade",
        required=True,
        domain=[("type", "in", ["product", "consu"])],
    )
    product_uom_id = fields.Many2one(
        "uom.uom",
        string="UoM",
        required=True,
        default=lambda self: self.env.ref("uom.product_uom_unit").id,
    )
    product_uom_qty = fields.Float(string="Quantity", required=True, default=1.0)


class ExportShipmentLot(models.Model):
    _name = "export.shipment.lot"
    _description = "Export Shipment Reserved Lot"
    _order = "id asc"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    line_id = fields.Many2one("export.shipment.line", string="Related Line", ondelete="set null")
    product_id = fields.Many2one("product.product", string="Product", required=True)
    lot_id = fields.Many2one(
        "stock.lot",
        string="Lot",
        required=True,
        domain="[('product_id', '=', product_id)]",
    )
    qty = fields.Float(string="Reserved Qty", required=True, default=0.0)
