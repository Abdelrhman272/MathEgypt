from odoo import fields, models


class PurchaseOrder(models.Model):
    _inherit = "purchase.order"

    farm_evaluation_id = fields.Many2one("farm.evaluation", string="Farm Evaluation", copy=False)


class PurchaseOrderLine(models.Model):
    _inherit = "purchase.order.line"

    farm_grade = fields.Char(string="Farm Grade", copy=False)
    farm_evaluation_line_id = fields.Many2one("farm.evaluation.line", string="Evaluation Line", copy=False)
