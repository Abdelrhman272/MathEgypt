
from odoo import models, fields

class AgriFarmEvaluation(models.Model):
    _name = 'agri.farm.evaluation'
    _description = 'Farm Evaluation'
    _inherit = ['mail.thread']

    name = fields.Char(default=lambda self: self.env['ir.sequence'].next_by_code('agri.farm.evaluation'), readonly=True)
    farm_id = fields.Many2one('agri.farm', required=True)
    crop_id = fields.Many2one('agri.crop')
    evaluation_date = fields.Date(required=True)
    expected_qty = fields.Float()
    expected_uom_id = fields.Many2one('uom.uom')
    purchase_id = fields.Many2one('purchase.order', readonly=True)
    state = fields.Selection([('draft','Draft'),('confirmed','Confirmed'),('cancelled','Cancelled')], default='draft', tracking=True)
    note = fields.Text()
