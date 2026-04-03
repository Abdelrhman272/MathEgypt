from odoo import api, fields, models


class AgxFarm(models.Model):
    _name = "agx.farm"
    _description = "Farm"
    _inherit = ["mail.thread", "mail.activity.mixin"]
    _order = "name"

    name = fields.Char(required=True, tracking=True)
    code = fields.Char(copy=False)
    partner_id = fields.Many2one("res.partner", string="Owner / Vendor", tracking=True)
    company_id = fields.Many2one("res.company", required=True, default=lambda self: self.env.company, index=True)
    region = fields.Char()
    location = fields.Char()
    active = fields.Boolean(default=True)
    note = fields.Html()


class AgxCrop(models.Model):
    _name = "agx.crop"
    _description = "Crop / Variety"
    _order = "name"

    name = fields.Char(required=True)
    code = fields.Char()
    active = fields.Boolean(default=True)
    note = fields.Text()


class AgxGrade(models.Model):
    _name = "agx.grade"
    _description = "Grade"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()


class AgxSize(models.Model):
    _name = "agx.size"
    _description = "Size"
    _order = "number, id"

    number = fields.Float(required=True)
    name = fields.Char(compute="_compute_name", store=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()

    @api.depends("number")
    def _compute_name(self):
        for rec in self:
            if rec.number == int(rec.number):
                rec.name = str(int(rec.number))
            else:
                rec.name = str(rec.number)

class AgxDestination(models.Model):
    _name = "agx.destination"
    _description = "Destination"
    _order = "name"

    name = fields.Char(required=True)
    country_id = fields.Many2one("res.country")
    port_name = fields.Char()
    active = fields.Boolean(default=True)
    note = fields.Text()


class AgxShipmentCostType(models.Model):
    _name = "agx.shipment.cost.type"
    _description = "Shipment Cost Type"
    _order = "sequence, name"

    name = fields.Char(required=True)
    code = fields.Selection([
        ("inland", "Inland Transport"),
        ("port", "Port Charges"),
        ("ocean", "Ocean Freight"),
        ("customs", "Customs Clearance"),
        ("other", "Other"),
    ], required=True, default="other")
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    default_allocation_basis = fields.Selection([
        ("qty", "By Quantity"),
        ("carton", "By Cartons"),
        ("net_weight", "By Net Weight"),
        ("gross_weight", "By Gross Weight"),
        ("equal", "Equal Share"),
    ], default="qty", required=True)
    note = fields.Text()
