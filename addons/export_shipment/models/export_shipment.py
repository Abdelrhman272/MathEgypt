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
    partner_id = fields.Many2one(
        "res.partner", string="Customer", required=True, domain=[("customer_rank", ">", 0)], tracking=True
    )

    container_no = fields.Char(string="Container No", tracking=True)
    seal_no = fields.Char(string="Seal No", tracking=True)
    destination = fields.Char(string="Destination", tracking=True)

    state = fields.Selection(
        [
            ("draft", "Draft"),
            ("reserved", "Lots Reserved"),
            ("done", "Done"),
            ("cancel", "Cancelled"),
        ],
        default="draft",
        tracking=True,
    )

    line_ids = fields.One2many("export.shipment.line", "shipment_id", string="Lines", copy=True)
    lot_line_ids = fields.One2many("export.shipment.lot", "shipment_id", string="Reserved Lots", copy=True)

    total_qty = fields.Float(string="Total Qty", compute="_compute_total_qty", store=False)

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

    def action_mark_reserved(self):
        for rec in self:
            if not rec.lot_line_ids:
                raise UserError(_("Add reserved lots first."))
            rec.state = "reserved"

    def action_cancel(self):
        self.write({"state": "cancel"})

    def action_reset_to_draft(self):
        self.write({"state": "draft"})


class ExportShipmentLine(models.Model):
    _name = "export.shipment.line"
    _description = "Export Shipment Line"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    product_id = fields.Many2one("product.product", string="Product/Grade", required=True)
    product_uom_id = fields.Many2one(
        "uom.uom", string="UoM", required=True, default=lambda self: self.env.ref("uom.product_uom_unit").id
    )
    product_uom_qty = fields.Float(string="Quantity", required=True, default=1.0)


class ExportShipmentLot(models.Model):
    _name = "export.shipment.lot"
    _description = "Export Shipment Reserved Lot"

    shipment_id = fields.Many2one("export.shipment", required=True, ondelete="cascade")
    line_id = fields.Many2one("export.shipment.line", string="Related Line", ondelete="set null")
    product_id = fields.Many2one("product.product", string="Product", required=True)
    lot_id = fields.Many2one("stock.lot", string="Lot", required=True, domain="[('product_id', '=', product_id)]")
    qty = fields.Float(string="Reserved Qty", required=True, default=0.0)
