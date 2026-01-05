
from odoo import models, fields

class AgriFarm(models.Model):
    _name = 'agri.farm'
    _description = 'Agricultural Farm'

    name = fields.Char(required=True)
    partner_id = fields.Many2one('res.partner', required=True)
    country_id = fields.Many2one('res.country')
    active = fields.Boolean(default=True)
    note = fields.Text()
