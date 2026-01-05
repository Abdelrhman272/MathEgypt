
from odoo import models, fields

class AgriCrop(models.Model):
    _name = 'agri.crop'
    _description = 'Crop / Variety'

    name = fields.Char(required=True)
    code = fields.Char()
    active = fields.Boolean(default=True)
