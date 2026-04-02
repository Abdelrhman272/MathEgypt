from odoo import fields, models


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


class ResConfigSettings(models.TransientModel):
    _inherit = "res.config.settings"

    agx_container_service_product_id = fields.Many2one(related="company_id.agx_container_service_product_id", readonly=False)
    agx_so_line_prefix = fields.Char(related="company_id.agx_so_line_prefix", readonly=False)
    agx_auto_generate_lot_numbers = fields.Boolean(related="company_id.agx_auto_generate_lot_numbers", readonly=False)
    agx_default_logistics_basis = fields.Selection(related="company_id.agx_default_logistics_basis", readonly=False)
    agx_allow_vendor_bill_cost_source = fields.Boolean(related="company_id.agx_allow_vendor_bill_cost_source", readonly=False)
    agx_allow_landed_cost_source = fields.Boolean(related="company_id.agx_allow_landed_cost_source", readonly=False)
    agx_margin_precision = fields.Integer(related="company_id.agx_margin_precision", readonly=False)
