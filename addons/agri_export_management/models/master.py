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
    _order = "sequence, number, id"

    name = fields.Char(required=True, copy=False)
    number = fields.Float(required=True)
    code = fields.Char()
    sequence = fields.Integer(default=10)
    active = fields.Boolean(default=True)
    note = fields.Text()

    @api.onchange('number')
    def _onchange_number(self):
        for rec in self:
            if rec.number:
                rec.name = str(int(rec.number)) if float(rec.number).is_integer() else str(rec.number)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            number = vals.get('number')
            if number not in (None, False):
                vals['name'] = str(int(number)) if float(number).is_integer() else str(number)
        return super().create(vals_list)

    def write(self, vals):
        if 'number' in vals:
            number = vals.get('number')
            vals['name'] = str(int(number)) if float(number).is_integer() else str(number)
        return super().write(vals)


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
