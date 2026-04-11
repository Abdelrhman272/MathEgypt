from odoo import api, fields, models
from odoo.exceptions import ValidationError


class ResCompany(models.Model):
    _inherit = "res.company"

    agx_container_service_product_id = fields.Many2one("product.product", string="Container Service Product")
    agx_so_line_prefix = fields.Char(default="Shipment")
    agx_auto_generate_lot_numbers = fields.Boolean(default=True)
    agx_default_logistics_basis = fields.Selection([
        ("qty", "By Quantity"),
        ("carton", "By Cartons"),
        ("net_weight", "By Net Weight"),
        ("gross_weight", "By Gross Weight"),
        ("equal", "Equal Share"),
    ], default="qty")
    agx_allow_vendor_bill_cost_source = fields.Boolean(default=True)
    agx_allow_landed_cost_source = fields.Boolean(default=True)
    agx_margin_precision = fields.Integer(default=2)
    agx_production_location_id = fields.Many2one("stock.location", string="Production Location")
    agx_finished_goods_location_id = fields.Many2one("stock.location", string="Finished Goods Location")
    agx_raw_material_location_id = fields.Many2one("stock.location", string="Raw Material Location")
    agx_internal_picking_type_id = fields.Many2one("stock.picking.type", string="Internal Transfer Type")
    agx_outgoing_picking_type_id = fields.Many2one("stock.picking.type", string="Outgoing Delivery Type")

    @api.constrains(
        "agx_production_location_id",
        "agx_finished_goods_location_id",
        "agx_raw_material_location_id",
        "agx_internal_picking_type_id",
        "agx_outgoing_picking_type_id",
    )
    def _check_agx_stock_configuration(self):
        for rec in self:
            if rec.agx_raw_material_location_id:
                if rec.agx_raw_material_location_id.usage != "internal":
                    raise ValidationError("Raw Material Location must be an internal location.")
                if rec.agx_raw_material_location_id.company_id and rec.agx_raw_material_location_id.company_id != rec:
                    raise ValidationError("Raw Material Location must belong to the same company.")
            if rec.agx_finished_goods_location_id:
                if rec.agx_finished_goods_location_id.usage != "internal":
                    raise ValidationError("Finished Goods Location must be an internal location.")
                if rec.agx_finished_goods_location_id.company_id and rec.agx_finished_goods_location_id.company_id != rec:
                    raise ValidationError("Finished Goods Location must belong to the same company.")
            if rec.agx_production_location_id:
                if rec.agx_production_location_id.usage not in ("production", "internal"):
                    raise ValidationError("Production Location must be a production or internal location.")
                if rec.agx_production_location_id.company_id and rec.agx_production_location_id.company_id != rec:
                    raise ValidationError("Production Location must belong to the same company.")
            if rec.agx_internal_picking_type_id:
                if rec.agx_internal_picking_type_id.code != "internal":
                    raise ValidationError("Internal Transfer Type must be of code 'internal'.")
                if rec.agx_internal_picking_type_id.company_id and rec.agx_internal_picking_type_id.company_id != rec:
                    raise ValidationError("Internal Transfer Type must belong to the same company.")
            if rec.agx_outgoing_picking_type_id:
                if rec.agx_outgoing_picking_type_id.code != "outgoing":
                    raise ValidationError("Outgoing Delivery Type must be of code 'outgoing'.")
                if rec.agx_outgoing_picking_type_id.company_id and rec.agx_outgoing_picking_type_id.company_id != rec:
                    raise ValidationError("Outgoing Delivery Type must belong to the same company.")


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    agx_container_service_product_id = fields.Many2one(related="company_id.agx_container_service_product_id", readonly=False)
    agx_so_line_prefix = fields.Char(related="company_id.agx_so_line_prefix", readonly=False)
    agx_auto_generate_lot_numbers = fields.Boolean(related="company_id.agx_auto_generate_lot_numbers", readonly=False)
    agx_default_logistics_basis = fields.Selection(related="company_id.agx_default_logistics_basis", readonly=False)
    agx_allow_vendor_bill_cost_source = fields.Boolean(related="company_id.agx_allow_vendor_bill_cost_source", readonly=False)
    agx_allow_landed_cost_source = fields.Boolean(related="company_id.agx_allow_landed_cost_source", readonly=False)
    agx_margin_precision = fields.Integer(related="company_id.agx_margin_precision", readonly=False)
    agx_production_location_id = fields.Many2one(related="company_id.agx_production_location_id", readonly=False)
    agx_finished_goods_location_id = fields.Many2one(related="company_id.agx_finished_goods_location_id", readonly=False)
    agx_raw_material_location_id = fields.Many2one(related="company_id.agx_raw_material_location_id", readonly=False)
    agx_internal_picking_type_id = fields.Many2one(related="company_id.agx_internal_picking_type_id", readonly=False)
    agx_outgoing_picking_type_id = fields.Many2one(related="company_id.agx_outgoing_picking_type_id", readonly=False)
