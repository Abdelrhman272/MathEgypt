from odoo import models, fields

class MrpProduction(models.Model):
    _inherit = "mrp.production"

    farm_evaluation_id = fields.Many2one(
        "farm.evaluation",
        string="Farm Evaluation",
        tracking=True,
    )
